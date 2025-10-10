FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Установка дополнительных пакетов для обработки медиа
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Установка переменных окружения
ENV FLASK_PORT=5000
ENV PORT=5000

# Запуск Flask API
CMD ["python", "-m", "flask", "--app", "bot:flask_app", "run", "--host=0.0.0.0", "--port=5000"]
