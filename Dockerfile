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
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка переменных окружения
ENV FLASK_PORT=5000
ENV PORT=5000

# Используем gunicorn для запуска Flask приложения
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "bot:flask_app"]
