FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Установка дополнительных пакетов для обработки медиа
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-rus \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Запуск и бота и Flask API
CMD ["sh", "-c", "python bot.py & python -m flask --app bot:flask_app run --host=0.0.0.0 --port=$PORT"]
