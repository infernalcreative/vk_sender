import os
import time
import logging
import asyncio
import io
from fastapi import FastAPI, Request, HTTPException, Header
from vk_api import VkApi
from vk_api.exceptions import ApiError
from vk_api.utils import get_random_id
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from PIL import Image

DEBUG_VK = os.getenv("DEBUG_VK", "false").lower() in ("true", "1", "yes")
LOG_LEVEL = logging.DEBUG if DEBUG_VK else logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

MAX_VK_MESSAGE_LENGTH = 4048
MAX_FILE_SIZE_MB = 10

if DEBUG_VK:
    logger.warning("DEBUG_VK is ENABLED: Extended VK API logs and raw responses WILL be printed.")

def log_debug(msg, data=None):
    if not DEBUG_VK:
        return
    if data is not None:
        import json
        try:
            if isinstance(data, (dict, list)):
                serialized = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                serialized = str(data)
            # Ограничиваем длину вывода в лог, чтобы не перегружать консоль
            truncated = serialized[:4096] + "\n... [TRUNCATED]" if len(serialized) > 4096 else serialized
            logger.debug(f"{msg}:\n{truncated}")
        except Exception:
            logger.debug(f"{msg}: [non-serializable data]")
    else:
        logger.debug(msg)

TIMEOUT_DOWNLOAD = 90.0
TIMEOUT_UPLOAD_TO_VK = 180.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/250.0.0.0 Safari/537.36"
)

@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    retry=retry_if_exception_type((
        httpx.ReadTimeout,
        httpx.ConnectError,
        httpx.NetworkError,
        ValueError
    )),
    reraise=True,
)
async def upload_file_to_vk_upload_url(upload_url, photo_file):
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(timeout=TIMEOUT_UPLOAD_TO_VK) as client:
        start_time = time.time()
        logger.info("Starting upload to VK...")
        resp = await client.post(upload_url, files=photo_file, headers=headers)
        duration = time.time() - start_time
        logger.info(f"Upload completed in {duration:.2f}s, status={resp.status_code}")
        
        log_debug("VK Upload Response Headers", dict(resp.headers))
        log_debug("VK Upload Raw Response Body", resp.text)

        if resp.status_code != 200:
            raise ValueError(f"Upload failed with status {resp.status_code}")

        try:
            upload_data = resp.json()
        except Exception as e:
            logger.error(f"Failed to parse VK response as JSON. Raw body: {resp.text}")
            raise ValueError(f"Upload response is not valid JSON: {e}")

        required_keys = ["server", "photo", "hash"]
        missing = [k for k in required_keys if k not in upload_data or not upload_data[k] or upload_data[k] == "[]"]

        if missing:
            logger.warning(f"VK uploaded fields missing or empty: {missing}. Full response was: {upload_data}")
            raise ValueError(f"VK response validation failed. Missing: {missing}")

        return upload_data

def process_and_compress_image(file_data: bytes) -> bytes:
    """CPU-bound операция: сжатие изображения."""
    img = Image.open(io.BytesIO(file_data))
    img.thumbnail((1280, 720))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return buf.getvalue()

@app.post("/notify")
async def notify(
    request: Request,
    x_vk_token: str = Header(..., alias="X-VK-Token"),
    x_chat_id: str = Header(..., alias="X-Chat-ID")
):
    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Invalid JSON in request: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = data.get("message")
    attach_url = data.get("attach")

    if not message or not isinstance(message, str):
        raise HTTPException(status_code=400, detail="'message' field is required and must be a string")

    try:
        chat_id_int = int(x_chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="'X-Chat-ID' header must be a valid integer")

    peer_id = 2000000000 + chat_id_int

    if len(message) > MAX_VK_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"'message' exceeds allowed limit of {MAX_VK_MESSAGE_LENGTH} characters"
        )

    vk = VkApi(token=x_vk_token).get_api()
    attachment_str = None
    file_data = None

    # 1. Скачивание и обработка вложения
    if attach_url:
        if not isinstance(attach_url, str):
            raise HTTPException(status_code=400, detail="'attach' must be a string URL")

        try:
            logger.info(f"Downloading attachment from: {attach_url}")
            async with httpx.AsyncClient(timeout=TIMEOUT_DOWNLOAD) as client:
                resp = await client.get(attach_url, headers={"User-Agent": DEFAULT_USER_AGENT})
                resp.raise_for_status()
                file_data = resp.content

            if not file_data:
                raise ValueError("Empty file downloaded")

            file_size_mb = len(file_data) / (1024 * 1024)
            logger.info(f"Downloaded file size: {file_size_mb:.2f} MB")

            # Сжимаем только если картинка больше 1.5 МБ
            if len(file_data) > 1.5 * 1024 * 1024:
                logger.info("Applying compression to image...")
                file_data = await asyncio.to_thread(process_and_compress_image, file_data)
                new_size = len(file_data) / (1024 * 1024)
                logger.info(f"Image compressed to {new_size:.2f} MB (was {file_size_mb:.2f} MB)")
            else:
                logger.info("Image is small enough, skipping compression.")

            if len(file_data) > MAX_FILE_SIZE_MB * 1024 * 1024:
                logger.error("File is too large even after compression limits.")
                raise HTTPException(status_code=413, detail="File size exceeds maximum limits")

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to download or process attachment: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch attachment from camera: {e}")

    # 2. Загрузка фото на сервера VK
    if file_data is not None:
        try:
            logger.info("Calling getMessagesUploadServer...")
            upload_server = await asyncio.to_thread(
                vk.photos.getMessagesUploadServer,
                peer_id=int(peer_id)
            )
            log_debug("getMessagesUploadServer response", upload_server)

            if "upload_url" not in upload_server:
                logger.error(f"VK did not return 'upload_url': {upload_server}")
                raise HTTPException(
                    status_code=500,
                    detail="VK API returned unexpected structure (missing upload_url)"
                )

            upload_url = upload_server["upload_url"]
            photo_file = {"file": ("image.jpg", file_data, "image/jpeg")}

            logger.info("Uploading photo to VK upload_url...")
            # Валидация ответа и дебаг-логи выполняются внутри функции с ретраями
            upload_data = await upload_file_to_vk_upload_url(upload_url, photo_file)

            logger.info("Calling saveMessagesPhoto...")
            save_resp = await asyncio.to_thread(
                vk.photos.saveMessagesPhoto,
                server=int(upload_data["server"]),
                photo=str(upload_data["photo"]),
                hash=str(upload_data["hash"])
            )
            log_debug("saveMessagesPhoto response", save_resp)

            if save_resp and len(save_resp) > 0:
                photo = save_resp[0]
                attachment_str = f"photo{photo['owner_id']}_{photo['id']}"
                logger.info(f"Attachment uploaded successfully: {attachment_str}")
            else:
                logger.warning(f"saveMessagesPhoto returned empty result. Full response: {save_resp}")
                raise HTTPException(status_code=502, detail="VK save photo returned empty result")

        except ApiError as e:
            log_debug("VK ApiError full dump", {"code": e.code, "message": str(e), "request": e.request})
            if e.code == 901:
                logger.error(f"VK Error 901: Can't send messages for users without permission (peer_id: {peer_id})")
                raise HTTPException(status_code=403, detail="VK Error 901: No permission to send message")
            else:
                logger.exception(f"VK API error while processing attachment: {e}")
                raise HTTPException(status_code=502, detail=f"VK API error during attachment save: {e}")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error processing attachment: {e}")
            raise HTTPException(status_code=500, detail="Internal error during attachment upload")

    # 3. Отправка сообщения
    try:
        send_kwargs = {
            "chat_id": chat_id_int,
            "message": message,
            "random_id": get_random_id()
        }
        if attachment_str:
            send_kwargs["attachment"] = attachment_str
        elif attach_url:
            raise HTTPException(status_code=500, detail="Attachment was requested but could not be processed")

        logger.info("Sending message to VK...")
        await asyncio.to_thread(vk.messages.send, **send_kwargs)
        return {"status": "ok", "chat_id": chat_id_int, "has_attachment": bool(attachment_str)}

    except ApiError as e:
        log_debug("VK ApiError send dump", {"code": e.code, "message": str(e)})
        logger.exception(f"Failed to send message to VK (API Error): {e}")
        raise HTTPException(status_code=500, detail=f"VK API error during messages.send: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error while sending message")
        raise HTTPException(status_code=500, detail="Failed to send to VK")
                
