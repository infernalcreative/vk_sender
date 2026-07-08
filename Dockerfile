FROM python:3.11-slim

RUN pip install --no-cache-dir fastapi uvicorn vk_api

COPY vk_sender.py /app/vk_sender.py
WORKDIR /app

CMD ["uvicorn", "vk_sender:app", "--host", "0.0.0.0", "--port", "8000"]
