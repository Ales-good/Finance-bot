import sqlite3
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import json
import tempfile
import re
import io
import subprocess
import sys
from PIL import Image
import speech_recognition as sr
import torch
import torchaudio
import numpy as np
import psycopg2
from urllib.parse import urlparse
import logging

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== НАСТРОЙКА БАЗЫ ДАННЫХ =====
def get_db_connection():
    """Подключение к базе данных (PostgreSQL в продакшене, SQLite в разработке)"""
    if 'DATABASE_URL' in os.environ:
        # Production - PostgreSQL
        try:
            database_url = os.environ['DATABASE_URL']
            parsed_url = urlparse(database_url)
            conn = psycopg2.connect(
                database=parsed_url.path[1:],
                user=parsed_url.username,
                password=parsed_url.password,
                host=parsed_url.hostname,
                port=parsed_url.port,
                sslmode='require'
            )
            logger.info("✅ Подключено к PostgreSQL")
            return conn
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}")
            # Fallback to SQLite
            return sqlite3.connect('finance.db', check_same_thread=False)
    else:
        # Development - SQLite
        return sqlite3.connect('finance.db', check_same_thread=False)

def init_db():
    """Инициализация базы данных"""
    conn = get_db_connection()
    
    if isinstance(conn, sqlite3.Connection):
        # SQLite
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS expenses
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER, 
                      user_name TEXT,
                      amount REAL, 
                      category TEXT, 
                      description TEXT, 
                      date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    else:
        # PostgreSQL
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS expenses
                     (id SERIAL PRIMARY KEY,
                      user_id BIGINT, 
                      user_name TEXT,
                      amount DECIMAL, 
                      category TEXT, 
                      description TEXT, 
                      date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

def add_expense(user_id, user_name, amount, category, description=""):
    """Добавление траты в базу"""
    try:
        logger.info(f"💾 Сохраняем в базу: {user_name} - {amount} руб - {category} - {description}")
        
        conn = get_db_connection()
        c = conn.cursor()
        
        if isinstance(conn, sqlite3.Connection):
            # SQLite
            c.execute('''INSERT INTO expenses (user_id, user_name, amount, category, description) 
                         VALUES (?, ?, ?, ?, ?)''',
                      (user_id, user_name, amount, category, description))
        else:
            # PostgreSQL
            c.execute('''INSERT INTO expenses (user_id, user_name, amount, category, description) 
                         VALUES (%s, %s, %s, %s, %s)''',
                      (user_id, user_name, amount, category, description))
        
        conn.commit()
        conn.close()
        logger.info(f"✅ Добавлена трата: {user_name} - {amount} руб - {category}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении в базу: {str(e)}")

# ===== TESSERACT OCR ИНИЦИАЛИЗАЦИЯ =====
def check_tesseract_installation():
    """Проверяем установлен ли Tesseract"""
    try:
        result = subprocess.run(['tesseract', '--version'], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("✅ Tesseract доступен в PATH")
            return True
        else:
            logger.warning("❌ Tesseract не найден в PATH")
            return False
    except FileNotFoundError:
        logger.warning("❌ Tesseract не установлен или не добавлен в PATH")
        return False

# Проверяем перед запуском
if not check_tesseract_installation():
    logger.warning("⚠️ Установите Tesseract OCR и перезагрузите компьютер!")
    logger.warning("📥 Скачайте с: https://github.com/UB-Mannheim/tesseract/wiki")
    
logger.info("🔄 Проверяю Tesseract OCR...")
try:
    import pytesseract
    # Настройка пути к Tesseract (для Windows)
    if os.name == 'nt':  # Windows
        pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    
    # Проверяем доступность
    pytesseract.get_tesseract_version()
    TESSERACT_AVAILABLE = True
    logger.info("✅ Tesseract OCR доступен")
except Exception as e:
    TESSERACT_AVAILABLE = False
    logger.warning(f"❌ Tesseract OCR недоступен: {e}")

# ===== ГОЛОСОВОЕ РАСПОЗНАВАНИЕ =====
class VoiceRecognizer:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.pause_threshold = 0.8
        self.recognizer.dynamic_energy_threshold = True
        
        # Проверяем доступность Google Speech Recognition
        try:
            # Тестовый запрос
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=1)
            self.google_available = True
            logger.info("✅ Google Speech Recognition доступен")
        except:
            self.google_available = False
            logger.warning("⚠️ Google Speech Recognition недоступен, будет использоваться Vosk")
        
        # Пробуем загрузить Vosk как резервный вариант
        try:
            import vosk
            # Скачиваем модель если нужно
            model_path = "vosk-model-small-ru-0.22"
            if not os.path.exists(model_path):
                logger.info("📥 Скачиваю модель Vosk...")
                import urllib.request
                import zipfile
                url = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
                urllib.request.urlretrieve(url, "model.zip")
                with zipfile.ZipFile("model.zip", 'r') as zip_ref:
                    zip_ref.extractall(".")
                os.remove("model.zip")
            
            self.vosk_model = vosk.Model(model_path)
            self.vosk_available = True
            logger.info("✅ Vosk распознавание доступен")
        except Exception as e:
            logger.warning(f"⚠️ Vosk недоступен: {e}")
            self.vosk_available = False
    
    async def transcribe_audio(self, audio_path):
        """Транскрибируем аудио файл"""
        logger.info(f"🎤 Распознаю аудио: {audio_path}")
        
        # Сначала пробуем Google (самый точный)
        if self.google_available:
            try:
                with sr.AudioFile(audio_path) as source:
                    audio = self.recognizer.record(source)
                    text = self.recognizer.recognize_google(audio, language='ru-RU')
                    logger.info(f"✅ Google распознал: {text}")
                    return text
            except sr.UnknownValueError:
                logger.warning("❌ Google не смог распознать аудио")
            except sr.RequestError as e:
                logger.warning(f"❌ Ошибка Google API: {e}")
        
        # Пробуем Vosk как резервный вариант
        if self.vosk_available:
            try:
                return self._transcribe_with_vosk(audio_path)
            except Exception as e:
                logger.warning(f"❌ Ошибка Vosk: {e}")
        
        # Если ничего не работает, используем встроенное распознавание Telegram
        return None
    
    def _transcribe_with_vosk(self, audio_path):
        """Распознавание через Vosk"""
        import wave
        import json
        import vosk
        
        # Конвертируем в WAV если нужно
        wav_path = audio_path
        if not audio_path.endswith('.wav'):
            wav_path = audio_path.replace('.ogg', '.wav')
            subprocess.run(['ffmpeg', '-i', audio_path, '-ar', '16000', '-ac', '1', '-y', wav_path], 
                         capture_output=True)
        
        # Распознаем через Vosk
        wf = wave.open(wav_path, 'rb')
        rec = vosk.KaldiRecognizer(self.vosk_model, wf.getframerate())
        
        results = []
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                if result['text']:
                    results.append(result['text'])
        
        final_result = json.loads(rec.FinalResult())
        if final_result['text']:
            results.append(final_result['text'])
        
        wf.close()
        
        # Удаляем временный файл если создавали
        if wav_path != audio_path and os.path.exists(wav_path):
            os.remove(wav_path)
        
        text = ' '.join(results)
        logger.info(f"✅ Vosk распознал: {text}")
        return text if text.strip() else None

# Инициализируем распознаватель голоса
voice_recognizer = VoiceRecognizer()

# ===== ПРОСТОЙ ML КЛАССИФИКАТОР =====
class SimpleExpenseClassifier:
    def __init__(self):
        self.categories = ['Продукты', 'Кафе', 'Транспорт', 'Дом', 'Одежда', 'Здоровье', 'Развлечения', 'Подписки', 'Маркетплейсы', 'Другое']
        logger.info("✅ Простой классификатор инициализирован")
    
    def predict_category(self, text):
        """Простой rule-based классификатор категорий"""
        if not text:
            return "Другое", 0.0
            
        text_lower = text.lower()
        
        # Ключевые слова для каждой категории
        keyword_categories = {
            'Продукты': ['продукт', 'еда', 'магазин', 'супермаркет', 'покупк'],
            'Кафе': ['кафе', 'ресторан', 'кофе', 'обед', 'ужин'],
            'Транспорт': ['транспорт', 'такси', 'метро', 'автобус', 'бензин'],
            'Дом': ['дом', 'квартир', 'коммунал', 'аренд', 'ремонт'],
            'Одежда': ['одежд', 'обув', 'шопинг', 'вещ', 'магазин'],
            'Здоровье': ['здоров', 'аптек', 'врач', 'лекарств', 'больнич'],
            'Развлечения': ['развлечен', 'кино', 'концерт', 'театр', 'клуб'],
            'Подписки': ['подписк', 'интернет', 'телефон', 'связ', 'мобильн'],
            'Маркетплейсы': ['wildberries', 'озон', 'яндекс маркет', 'алиэкспресс'],
        }
        
        scores = {}
        for category, keywords in keyword_categories.items():
            score = sum(1 for keyword in keywords if keyword in text_lower)
            scores[category] = score
        
        best_category = max(scores, key=scores.get)
        confidence = scores[best_category] / max(1, len(text_lower.split()))
        
        return best_category if scores[best_category] > 0 else "Другое", min(confidence, 1.0)

# Инициализируем классификатор
classifier = SimpleExpenseClassifier()

# ===== УЛУЧШЕННОЕ РАСПОЗНАВАНИЕ ЧЕКОВ =====
def parse_receipt_text(text):
    """Улучшенный парсинг распознанного текста чека"""
    logger.info("🔍 Анализирую текст чека...")
    
    lines = text.split('\n')
    receipt_data = {
        'total': 0,
        'store': None,
        'date': None,
        'raw_text': text
    }
    
    # Паттерны для поиска сумм
    total_patterns = [
        r'(?:итого|всего|сумма|к\s*оплате|total|итог)[^\d]*(\d+[.,]\d+)',
        r'(\d+[.,]\d+)\s*(?:руб|р|₽|rur|rub|r|рублей)',
    ]
    
    # Поиск по паттернам
    for line in lines:
        line_clean = re.sub(r'[^\w\s\d.,]', '', line.lower())
        
        for pattern in total_patterns:
            matches = re.findall(pattern, line_clean)
            if matches:
                try:
                    amount_str = matches[-1].replace(',', '.')
                    amount_str = re.sub(r'[^\d.]', '', amount_str)
                    amount = float(amount_str)
                    if 1 <= amount <= 100000 and amount > receipt_data['total']:
                        receipt_data['total'] = amount
                        logger.info(f"💰 Найдена сумма: {amount}")
                        break
                except ValueError:
                    continue
    
    return receipt_data

async def process_receipt_photo(image_bytes):
    """Обрабатываем фото чека через Tesseract"""
    if not TESSERACT_AVAILABLE:
        return None
    
    try:
        logger.info("🔍 Распознаю чек через Tesseract...")
        
        image = Image.open(io.BytesIO(image_bytes))
        
        # Улучшаем качество изображения
        width, height = image.size
        if width < 1000 or height < 1000:
            new_size = (width * 2, height * 2)
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        # Распознаем текст
        text = pytesseract.image_to_string(image, lang='rus+eng')
        
        if not text.strip():
            logger.warning("❌ Не удалось распознать текст")
            return None
        
        logger.info(f"✅ Распознано символов: {len(text)}")
        return parse_receipt_text(text)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки чека: {e}")
        return None

# ===== КЛАВИАТУРЫ =====
def get_main_keyboard():
    """Основная клавиатура"""
    web_app_url = os.environ.get('WEB_APP_URL', 'https://ales-good.github.io/Finance-bot/')
    keyboard = [
        [KeyboardButton("💸 Добавить трату", web_app=WebAppInfo(url=web_app_url))],
        [KeyboardButton("📊 Общая статистика"), KeyboardButton("📅 Статистика за месяц")],
        [KeyboardButton("📝 Последние траты"), KeyboardButton("📈 Выгрузить в Excel")],
        [KeyboardButton("🆘 Помощь"), KeyboardButton("🗑️ Очистить данные")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_simple_confirmation_keyboard():
    """Упрощенная клавиатура для подтверждения"""
    keyboard = [
        [KeyboardButton("✅ Да, сохранить"), KeyboardButton("❌ Отменить")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ===== ОСНОВНЫЕ КОМАНДЫ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    welcome_text = f"""
Привет, {user.first_name}! 👋

Я бот для учета финансов 💰 с расширенными возможностями!

**Основные возможности:**
• 💸 **Добавить трату** - удобная форма с калькулятором
• 🎤 **Голосовой ввод** - отправьте голосовое сообщение с тратой
• 📸 **Распознавание чеков** - отправьте фото чека для автоматического распознавания
• 📊 **Статистика** - графики по категориям и месяцам  
• 📝 **История** - последние траты
• 📈 **Excel** - выгрузка данных

Просто начните использовать кнопки ниже! ⬇️
"""
    
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())

async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("🎯 Получены данные из Web App")
        
        data = update.effective_message.web_app_data
        parsed_data = json.loads(data.data)
        user = update.effective_user
        
        logger.info(f"📊 Данные из Web App: {parsed_data}")
        
        amount = parsed_data.get('amount')
        category = parsed_data.get('category')
        description = parsed_data.get('description', '')
        
        add_expense(user.id, user.first_name, amount, category, description)
        
        response = f"""✅ **Трата добавлена через форму!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {amount} руб
📂 **Категория:** {category}"""
        
        if description:
            response += f"\n📝 **Комментарий:** {description}"
            
        await update.message.reply_text(response, reply_markup=get_main_keyboard())
        
    except Exception as e:
        logger.error(f"❌ Ошибка в обработчике Web App: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при сохранении данных из формы: {str(e)}",
            reply_markup=get_main_keyboard()
        )

# ===== ОБРАБОТЧИК ГОЛОСОВЫХ СООБЩЕНИЙ =====
async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        voice = update.message.voice
        
        processing_msg = await update.message.reply_text("🎤 Обрабатываю голосовое сообщение...")
        
        # Скачиваем голосовое сообщение
        voice_file = await voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        
        # Сохраняем временно
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
            temp_file.write(voice_bytes)
            temp_path = temp_file.name
        
        try:
            # Распознаем речь
            text = await voice_recognizer.transcribe_audio(temp_path)
            
            if not text:
                await update.message.reply_text(
                    "❌ Не удалось распознать голосовое сообщение.\n\n"
                    "Попробуйте:\n"
                    "• Говорить четче и громче\n"
                    "• Использовать текстовый ввод",
                    reply_markup=get_main_keyboard()
                )
                return
            
            await processing_msg.edit_text(f"🎤 Распознано: *{text}*", parse_mode='Markdown')
            
            # Простой парсинг текста
            words = text.lower().split()
            amount = None
            category = "Другое"
            description_words = []
            
            for word in words:
                # Пробуем извлечь сумму
                try:
                    if word.isdigit():
                        potential_amount = int(word)
                        if 1 <= potential_amount <= 100000:
                            amount = potential_amount
                            continue
                except:
                    pass
                
                # Определяем категорию
                if any(keyword in word for keyword in ['еда', 'продукт', 'магазин']):
                    category = "Продукты"
                elif any(keyword in word for keyword in ['кафе', 'ресторан', 'кофе']):
                    category = "Кафе"
                elif any(keyword in word for keyword in ['такси', 'транспорт', 'бензин']):
                    category = "Транспорт"
                else:
                    description_words.append(word)
            
            if amount:
                description = ' '.join(description_words) if description_words else "Голосовая трата"
                
                preview_text = f"""🎤 **Голосовая трата распознана!**

💸 **Сумма:** {amount} руб
📂 **Категория:** {category}"""
                
                if description:
                    preview_text += f"\n📝 **Комментарий:** {description}"
                
                await update.message.reply_text(
                    preview_text + "\n\nСохранить эту трату?",
                    reply_markup=get_simple_confirmation_keyboard()
                )
                
                context.user_data['pending_voice_expense'] = {
                    'amount': amount, 'category': category, 
                    'description': description, 'text': text
                }
                
            else:
                await update.message.reply_text(
                    f"❌ Не удалось распознать трату в тексте: *{text}*\n\n"
                    "Попробуйте ввести вручную: `500 продукты`",
                    reply_markup=get_main_keyboard()
                )
                
        finally:
            # Удаляем временный файл
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки голосового сообщения: {str(e)}")
        await update.message.reply_text(
            "❌ Ошибка при обработке голоса. Попробуйте текстовый ввод.",
            reply_markup=get_main_keyboard()
        )

# ===== ОБРАБОТЧИК ФОТО ЧЕКОВ =====
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not TESSERACT_AVAILABLE:
            await update.message.reply_text(
                "❌ Распознавание чеков недоступно.\n\n"
                "Вы можете ввести трату вручную через форму или текстом.",
                reply_markup=get_main_keyboard()
            )
            return
        
        user = update.effective_user
        photo = update.message.photo[-1]
        
        processing_msg = await update.message.reply_text("📸 Обрабатываю фото чека...")
        
        # Скачиваем фото
        photo_file = await photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        logger.info(f"📷 Получено фото: {len(photo_bytes)} байт")
        
        # Обрабатываем чек
        receipt_data = await process_receipt_photo(photo_bytes)
        
        if receipt_data and receipt_data['total'] > 0:
            category, confidence = classifier.predict_category(receipt_data.get('store', 'чек покупка'))
            description = f"Чек {receipt_data.get('store', '')}".strip()
            
            preview_text = f"""📸 **Чек распознан!**

💸 **Сумма:** {receipt_data['total']} руб
📂 **Категория:** {category}"""

            await processing_msg.delete()
            await update.message.reply_text(
                preview_text + "\n\nСохраняем трату?",
                reply_markup=get_simple_confirmation_keyboard()
            )
            
            context.user_data['pending_receipt_expense'] = {
                'amount': receipt_data['total'],
                'category': category,
                'description': description,
            }
            
        else:
            await processing_msg.delete()
            await update.message.reply_text(
                "❌ Не удалось распознать чек.\n\n"
                "Попробуйте сфотографировать чек более четко или ввести данные вручную.",
                reply_markup=get_main_keyboard()
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото: {str(e)}")
        try:
            await processing_msg.delete()
        except:
            pass
        await update.message.reply_text(
            "❌ Ошибка при обработке фото. Попробуйте текстовый ввод или форму.",
            reply_markup=get_main_keyboard()
        )

# ===== ОБРАБОТЧИКИ ПОДТВЕРЖДЕНИЯ =====
async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Универсальный обработчик подтверждения"""
    text = update.message.text
    user = update.effective_user
    
    # Проверяем голосовую трату
    if context.user_data.get('pending_voice_expense'):
        expense_data = context.user_data['pending_voice_expense']
        
        if text == "✅ Да, сохранить":
            add_expense(
                user.id, user.first_name, 
                expense_data['amount'], 
                expense_data['category'], 
                expense_data['description']
            )
            
            response = f"""✅ **Голосовая трата добавлена!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {expense_data['amount']} руб
📂 **Категория:** {expense_data['category']}"""
            
            if expense_data['description']:
                response += f"\n📝 **Комментарий:** {expense_data['description']}"
                
            await update.message.reply_text(response, reply_markup=get_main_keyboard())
            context.user_data.pop('pending_voice_expense', None)
            
        elif text == "❌ Отменить":
            await update.message.reply_text(
                "❌ Добавление голосовой траты отменено.",
                reply_markup=get_main_keyboard()
            )
            context.user_data.pop('pending_voice_expense', None)
        return
    
    # Проверяем трату из чека
    if context.user_data.get('pending_receipt_expense'):
        expense_data = context.user_data['pending_receipt_expense']
        
        if text == "✅ Да, сохранить":
            add_expense(
                user.id, user.first_name, 
                expense_data['amount'], 
                expense_data['category'], 
                expense_data['description']
            )
            
            response = f"""✅ **Трата добавлена из чека!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {expense_data['amount']} руб
📂 **Категория:** {expense_data['category']}"""
            
            if expense_data['description']:
                response += f"\n📝 **Комментарий:** {expense_data['description']}"
                
            await update.message.reply_text(response, reply_markup=get_main_keyboard())
            context.user_data.pop('pending_receipt_expense', None)
            
        elif text == "❌ Отменить":
            await update.message.reply_text(
                "❌ Добавление траты отменено.",
                reply_markup=get_main_keyboard()
            )
            context.user_data.pop('pending_receipt_expense', None)
        return

# ===== ОБРАБОТЧИК ТЕКСТА =====
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    logger.info(f"📨 Получено текстовое сообщение от {user.first_name}: {text}")
    
    # Обработка подтверждений
    if text in ["✅ Да, сохранить", "❌ Отменить"]:
        await handle_confirmation(update, context)
        return
    
    # Обработка кнопок главного меню
    if text == "📊 Общая статистика":
        await show_stats(update, context)
    elif text == "📅 Статистика за месяц":
        await show_monthly_stats(update, context)
    elif text == "📝 Последние траты":
        await show_list(update, context)
    elif text == "📈 Выгрузить в Excel":
        await export_excel(update, context)
    elif text == "🆘 Помощь":
        await show_help(update, context)
    elif text == "🗑️ Очистить данные":
        await clear_data(update, context)
    else:
        # Попытка распознать текстовую трату
        try:
            parts = text.split()
            if len(parts) >= 2:
                amount = float(parts[0].replace(',', '.'))
                category = parts[1].lower()
                description = " ".join(parts[2:]) if len(parts) > 2 else ""
                
                add_expense(user.id, user.first_name, amount, category, description)
                
                response = f"""✅ **Трата добавлена!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {amount} руб
📂 **Категория:** {category}"""
                
                if description:
                    response += f"\n📝 **Описание:** {description}"
                    
                await update.message.reply_text(response, reply_markup=get_main_keyboard())
                return
        except ValueError:
            pass
        
        # Если не распознали - показываем помощь
        await show_help(update, context)

# ===== СТАТИСТИКА И ДРУГИЕ ФУНКЦИИ =====
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        user_id = update.effective_user.id
        
        # Получаем статистику для текущего пользователя
        if isinstance(conn, sqlite3.Connection):
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total, COUNT(*) as count 
                FROM expenses 
                WHERE user_id = {user_id}
                GROUP BY category 
                ORDER BY total DESC
            ''', conn)
        else:
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total, COUNT(*) as count 
                FROM expenses 
                WHERE user_id = {user_id}
                GROUP BY category 
                ORDER BY total DESC
            ''', conn)
        
        if df.empty:
            await update.message.reply_text(
                "📊 Пока нет данных для статистики.\n"
                "Добавьте первую трату! 💸",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Создаем график
        plt.figure(figsize=(10, 6))
        plt.pie(df['total'], labels=df['category'], autopct='%1.1f%%')
        plt.title('📊 Распределение трат по категориям')
        
        # Сохраняем график
        chart_path = 'stats.png'
        plt.savefig(chart_path)
        plt.close()
        
        # Формируем текст статистики
        total_spent = df['total'].sum()
        stats_text = f"""📈 **ВАША СТАТИСТИКА**

💰 **Всего потрачено:** {total_spent:,.0f} руб
📝 **Количество трат:** {df['count'].sum()}

**📋 Детали по категориям:**
"""
        
        for _, row in df.iterrows():
            percentage = (row['total'] / total_spent) * 100
            stats_text += f"• {row['category']}: {row['total']:,.0f} руб ({percentage:.1f}%)\n"
        
        # Отправляем сообщение с графиком
        with open(chart_path, 'rb') as chart:
            await update.message.reply_photo(
                photo=chart,
                caption=stats_text,
                parse_mode='Markdown',
                reply_markup=get_main_keyboard()
            )
        
        # Удаляем временный файл
        os.remove(chart_path)
        
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при формировании статистики: {str(e)}",
            reply_markup=get_main_keyboard()
        )
    finally:
        conn.close()

async def show_monthly_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        user_id = update.effective_user.id
        current_month = datetime.now().strftime('%Y-%m')
        
        if isinstance(conn, sqlite3.Connection):
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total
                FROM expenses 
                WHERE user_id = {user_id} AND strftime('%Y-%m', date) = '{current_month}'
                GROUP BY category 
                ORDER BY total DESC
            ''', conn)
        else:
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total
                FROM expenses 
                WHERE user_id = {user_id} AND DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)
                GROUP BY category 
                ORDER BY total DESC
            ''', conn)
        
        if df.empty:
            await update.message.reply_text(
                f"📅 За текущий месяц пока нет трат.\n"
                "Добавьте первую трату этого месяца! 💸",
                reply_markup=get_main_keyboard()
            )
            return
        
        total_spent = df['total'].sum()
        stats_text = f"""📅 **СТАТИСТИКА ЗА ТЕКУЩИЙ МЕСЯЦ**

💰 **Всего потрачено:** {total_spent:,.0f} руб

**📋 Траты по категориям:**
"""
        
        for _, row in df.iterrows():
            percentage = (row['total'] / total_spent) * 100
            stats_text += f"• {row['category']}: {row['total']:,.0f} руб ({percentage:.1f}%)\n"
        
        await update.message.reply_text(stats_text, reply_markup=get_main_keyboard())
        
    except Exception as e:
        logger.error(f"❌ Ошибка месячной статистики: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при формировании статистики: {str(e)}",
            reply_markup=get_main_keyboard()
        )
    finally:
        conn.close()

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        user_id = update.effective_user.id
        
        if isinstance(conn, sqlite3.Connection):
            df = pd.read_sql_query(f'''
                SELECT amount, category, description, date
                FROM expenses 
                WHERE user_id = {user_id}
                ORDER BY date DESC 
                LIMIT 10
            ''', conn)
        else:
            df = pd.read_sql_query(f'''
                SELECT amount, category, description, date
                FROM expenses 
                WHERE user_id = {user_id}
                ORDER BY date DESC 
                LIMIT 10
            ''', conn)
        
        if df.empty:
            await update.message.reply_text(
                "📝 Пока нет добавленных трат.\n"
                "Добавьте первую трату! 💸",
                reply_markup=get_main_keyboard()
            )
            return
        
        list_text = "📝 **ПОСЛЕДНИЕ ТРАТЫ**\n\n"
        
        for _, row in df.iterrows():
            date = datetime.strptime(str(row['date']), '%Y-%m-%d %H:%M:%S').strftime('%d.%m %H:%M')
            list_text += f"💸 **{row['amount']} руб** - {row['category']}\n"
            
            if row['description']:
                list_text += f"   📋 {row['description']}\n"
            
            list_text += f"   📅 {date}\n\n"
        
        await update.message.reply_text(list_text, parse_mode='Markdown', reply_markup=get_main_keyboard())
        
    except Exception as e:
        logger.error(f"❌ Ошибка списка трат: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при получении списка трат: {str(e)}",
            reply_markup=get_main_keyboard()
        )
    finally:
        conn.close()

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        user_id = update.effective_user.id
        
        if isinstance(conn, sqlite3.Connection):
            df = pd.read_sql_query(f'''
                SELECT date, amount, category, description
                FROM expenses 
                WHERE user_id = {user_id}
                ORDER BY date DESC
            ''', conn)
        else:
            df = pd.read_sql_query(f'''
                SELECT date, amount, category, description
                FROM expenses 
                WHERE user_id = {user_id}
                ORDER BY date DESC
            ''', conn)
        
        if df.empty:
            await update.message.reply_text(
                "📈 Нет данных для выгрузки.",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Создаем Excel файл
        excel_path = 'expenses_export.xlsx'
        df.to_excel(excel_path, index=False)
        
        # Отправляем файл
        with open(excel_path, 'rb') as excel_file:
            await update.message.reply_document(
                document=excel_file,
                filename=f'expenses_export_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
                caption='📈 Выгрузка данных по тратам',
                reply_markup=get_main_keyboard()
            )
        
        # Удаляем временный файл
        os.remove(excel_path)
        
    except Exception as e:
        logger.error(f"❌ Ошибка выгрузки в Excel: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при выгрузке данных: {str(e)}",
            reply_markup=get_main_keyboard()
        )
    finally:
        conn.close()

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    try:
        conn = get_db_connection()
        
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('DELETE FROM expenses WHERE user_id = ?', (user.id,))
        else:
            c = conn.cursor()
            c.execute('DELETE FROM expenses WHERE user_id = %s', (user.id,))
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            "✅ Ваши данные успешно очищены!\n"
            "Начинаем с чистого листа 🎯",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка очистки данных: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при очистке данных: {str(e)}",
            reply_markup=get_main_keyboard()
        )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🆘 **ПОМОЩЬ ПО ИСПОЛЬЗОВАНИЮ**

💸 **Добавить трату:**
• Используйте кнопку с формой - удобный калькулятор
• Или пишите текстом: `500 продукты` или `1500 кафе обед`

🎤 **Голосовой ввод:**
• Отправьте голосовое сообщение с описанием траты
• Пример: "500 продукты хлеб молоко"

📸 **Фото чеков:**
• Отправьте фото чека для автоматического распознавания

📊 **Статистика:**
• **Общая статистика** - полная картина всех трат
• **Статистика за месяц** - анализ текущего месяца
• **Последние траты** - история операций

📈 **Excel** - полная выгрузка данных

🗑️ **Очистить данные** - удалить все ваши траты

**💡 Советы:**
• Используйте категории для лучшего анализа
• Регулярно проверяйте статистику
• Экспортируйте данные для ведения бюджета
"""
    await update.message.reply_text(help_text, reply_markup=get_main_keyboard())

# ===== ЗАПУСК БОТА =====
def main():
    # Инициализация базы данных
    init_db()
    
    # Получение токена из переменных окружения
    BOT_TOKEN = os.environ.get('BOT_TOKEN', '7911885739:AAGrMekWmLgz_ej8JDFqG-CbDA5Nie7vKFc')
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        return
    
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear_data))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Запуск бота
    port = int(os.environ.get('PORT', 8443))
    
    if 'RAILWAY_STATIC_URL' in os.environ or 'HEROKU_APP_NAME' in os.environ:
        # Production - используем вебхуки
        webhook_url = os.environ.get('WEBHOOK_URL', '')
        if webhook_url:
            application.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=BOT_TOKEN,
                webhook_url=f"{webhook_url}/{BOT_TOKEN}"
            )
        else:
            logger.info("🚀 Запуск в режиме polling (production)")
            application.run_polling()
    else:
        # Development - polling
        logger.info("🚀 Запуск в режиме polling (development)")
        application.run_polling()

if __name__ == "__main__":
    main()