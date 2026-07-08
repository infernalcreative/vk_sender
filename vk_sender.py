from fastapi import FastAPI, Request, HTTPException, Header
from vk_api import VkApi
from vk_api.utils import get_random_id

app = FastAPI()

MAX_VK_MESSAGE_LENGTH = 4096

@app.post("/notify")
async def notify(
    request: Request,
    x_vk_token: str = Header(..., alias="X-VK-Token"),   # ... значит «обязательно»    x_chat_id: str = Header(..., alias="X-Chat-ID")      # ... значит «обязательно»):
    # 1. Получаем сообщение из тела запроса
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = data.get("message")
    if not message or not isinstance(message, str):
        raise HTTPException(status_code=400, detail="'message' field is required and must be a string")

    # 2. Валидируем chat_id из заголовка
    try:
        target_chat_id = int(x_chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="'X-Chat-ID' header must be a valid integer")

    if len(message) > MAX_VK_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"'message' exceeds VK limit of {MAX_VK_MESSAGE_LENGTH} characters"
        )

    # 3. Токен уже валидирован как обязательный заголовок (FastAPI сам выбросит 422, если нет)
    token = x_vk_token

    # 4. Инициализируем VK API и отправляем
    try:
        vk = VkApi(token=token).get_api()
        vk.messages.send(
            chat_id=target_chat_id,
            message=message,
            random_id=get_random_id()
        )
        return {"status": "ok", "chat_id": target_chat_id}
    except Exception as e:
        # Не выводим токен в ответе, только факт ошибки
        raise HTTPException(status_code=500, detail="Failed to send to VK")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
