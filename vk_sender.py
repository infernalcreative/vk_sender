import logging
from fastapi import FastAPI, Request, HTTPException, Header
from vk_api import VkApi, exceptions
from vk_api.utils import get_random_id
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

MAX_VK_MESSAGE_LENGTH = 4096
MAX_FILE_SIZE_MB = 10

@app.post("/notify")
async def notify(
    request: Request,
    x_vk_token: str = Header(..., alias="X-VK-Token"),
    x_chat_id: str = Header(..., alias="X-Chat-ID")
):
    # Парсим JSON
    try:
        data = await request.json()
    except Exception:
        logger.error("Invalid JSON in request")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = data.get("message")
    attach_url = data.get("attach")

    if not message or not isinstance(message, str):
        raise HTTPException(status_code=400, detail="'message' field is required and must be a string")

    # Преобразуем ID чата из строки в число
    try:
        chat_id_int = int(x_chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="'X-Chat-ID' header must be a valid integer")

    # peer_id для VK API (чаты: 2000000000 + номер чата)
    peer_id = 2000000000 + chat_id_int

    if len(message) > MAX_VK_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"'message' exceeds VK limit of {MAX_VK_MESSAGE_LENGTH} characters"
        )

    token = x_vk_token
    vk = VkApi(token=token).get_api()
    attachment_str = None
    file_data = None

    # Скачиваем вложение один раз, если нужно
    if attach_url:
        if not isinstance(attach_url, str):
            raise HTTPException(status_code=400, detail="'attach' must be a string URL")

        try:
            logger.info(f"Downloading attachment from: {attach_url}")
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(attach_url)
                resp.raise_for_status()
                file_data = resp.content

            if len(file_data) == 0:
                raise ValueError("Empty file")

            if len(file_data) > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise ValueError(f"File too large (> {MAX_FILE_SIZE_MB} MB)")
        except Exception as e:
            logger.exception(f"Failed to download attachment: {e}")
            logger.warning("Attachment download failed; sending text-only message")            file_data = None

    # Загрузка фото
    if file_data is not None:
        try:
            upload_server = vk.photos.getMessagesUploadServer(peer_id=peer_id)

            # Ожидаем upload_url. Если его нет — значит, VK не дал корректный ответ            if "upload_url" not in upload_server:
                logger.error(f"VK did not return 'upload_url' field: {upload_server}")
                raise HTTPException(
                    status_code=500,
                    detail="VK API returned unexpected structure (missing upload_url)"
                )

            upload_url = upload_server["upload_url"]
            photo_file = {"file": ("image.jpg", file_data, "image/jpeg")}

            logger.info("Uploading photo to VK upload_url")
            upload_resp = httpx.post(upload_url, files=photo_file)
            upload_data = upload_resp.json()

            # Проверяем, что в ответе есть нужные поля
            for key in ["server", "photo", "hash"]:
                if key not in upload_data:
                    logger.error(f"Missing key in upload response: {key}, data={upload_data}")
                    raise ValueError(f"Upload response missing required fields: {key}")

            save_resp = vk.photos.saveMessagesPhoto(
                server=upload_data["server"],
                photo=upload_data["photo"],
                hash=upload_data["hash"]
            )

            if save_resp:
                photo = save_resp[0]
                attachment_str = f"photo{photo['owner_id']}_{photo['id']}"
                logger.info("Attachment uploaded successfully")

        except exceptions.ApiError as e:
            logger.exception(f"VK API error while processing attachment: {e}")
            logger.warning("Attachment failed; sending text-only message")
        except Exception as e:
            logger.exception(f"Unexpected error processing attachment: {e}")
            logger.warning("Attachment failed; sending text-only message")

    # Отправка сообщения (здесь используем обычный chat_id, НЕ peer_id!)
    try:
        send_kwargs = {
            "chat_id": chat_id_int,          # обычный ID чата (не 2000000000+)
            "message": message,
            "random_id": get_random_id()
        }
        if attachment_str:
            send_kwargs["attachment"] = attachment_str

        vk.messages.send(**send_kwargs)
        return {"status": "ok", "chat_id": chat_id_int, "has_attachment": bool(attachment_str)}

    except exceptions.ApiError as e:
        logger.exception(f"Failed to send message to VK: {e}")
        raise HTTPException(status_code=500, detail=f"VK API error: {e}")
    except Exception as e:
        logger.exception("Unexpected error while sending message")
        raise HTTPException(status_code=500, detail="Failed to send to VK")
        
