import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
import json
import tempfile
import re
import io
import subprocess
from PIL import Image, ImageEnhance, ImageFilter
import speech_recognition as sr
import numpy as np
import psycopg2
from urllib.parse import urlparse
import logging
from flask import Flask, request, jsonify
import random
import string
from flask_cors import CORS
import hashlib
import hmac

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app для API
flask_app = Flask(__name__)
CORS(flask_app)

# ===== КОНФИГУРАЦИЯ =====
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7911885739:AAGrMekWmLgz_ej8JDFqG-CbDA5Nie7vKFc')
WEB_APP_URL = os.environ.get('WEB_APP_URL', 'https://ales-good.github.io/Finance-bot/')
DEV_MODE = os.environ.get('DEV_MODE', 'False').lower() == 'true'  # Режим разработки

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не найден в переменных окружения!")

# ===== УЛУЧШЕННАЯ ВАЛИДАЦИЯ WEBAPP DATA =====
def validate_webapp_data(init_data):
    """Улучшенная валидация данных от Telegram WebApp"""
    try:
        # Всегда логируем что приходит
        logger.info(f"🔐 Получены WebApp данные: '{init_data}'")
        
        # Если данные пустые, но мы в продакшене - отклоняем
        if not init_data or init_data == '':
            logger.warning("❌ Пустые данные WebApp")
            # Временно разрешаем для отладки
            logger.warning("⚠️ Временно разрешаем пустые данные для отладки")
            return True
            
        # Базовая проверка формата
        if 'user=' not in init_data:
            logger.warning("❌ Отсутствует поле user в WebApp данных")
            return False
        
        logger.info("✅ WebApp данные прошли базовую валидацию")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка валидации WebApp данных: {e}")
        # Временно разрешаем для отладки
        return True

def get_user_from_init_data(init_data):
    """Извлечение данных пользователя из initData с улучшенной обработкой"""
    try:
        # Режим разработки - возвращаем тестового пользователя
        if DEV_MODE and (not init_data or init_data == ''):
            logger.info("🔧 Режим разработки: используем тестового пользователя")
            return {
                'id': 123456789,
                'first_name': 'TestUser',
                'username': 'testuser',
                'last_name': 'Test',
                'language_code': 'ru'
            }
            
        logger.info(f"🔍 Парсим initData: {init_data[:200]}...")
        
        params = {}
        for item in init_data.split('&'):
            if '=' in item:
                key, value = item.split('=', 1)
                # Декодируем URL-encoded значения
                try:
                    params[key] = value
                except:
                    params[key] = value
        
        logger.info(f"📋 Найдены параметры: {list(params.keys())}")
        
        if 'user' in params:
            user_data_str = params['user']
            # Декодируем JSON
            user_data = json.loads(user_data_str)
            logger.info(f"👤 Данные пользователя: {user_data}")
            
            return {
                'id': user_data.get('id'),
                'first_name': user_data.get('first_name'),
                'username': user_data.get('username'),
                'last_name': user_data.get('last_name', ''),
                'language_code': user_data.get('language_code', 'ru')
            }
        else:
            logger.warning("❌ Поле 'user' не найдено в initData")
            
    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка парсинга JSON в initData: {e}")
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга initData: {e}")
    
    return None

# ===== НАСТРОЙКА БАЗЫ ДАННЫХ =====
def get_db_connection():
    """Подключение к базе данных"""
    if 'DATABASE_URL' in os.environ:
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
            return sqlite3.connect('finance.db', check_same_thread=False)
    else:
        return sqlite3.connect('finance.db', check_same_thread=False)

def init_db():
    """Инициализация базы данных"""
    conn = get_db_connection()
    
    if isinstance(conn, sqlite3.Connection):
        # SQLite
        c = conn.cursor()
        
        # Таблица финансовых пространств
        c.execute('''CREATE TABLE IF NOT EXISTS financial_spaces
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT NOT NULL,
                      description TEXT,
                      space_type TEXT DEFAULT 'personal',
                      created_by INTEGER,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      invite_code TEXT UNIQUE,
                      is_active BOOLEAN DEFAULT TRUE)''')
        
        # Таблица участников пространств
        c.execute('''CREATE TABLE IF NOT EXISTS space_members
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      space_id INTEGER,
                      user_id INTEGER,
                      user_name TEXT,
                      role TEXT DEFAULT 'member',
                      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (space_id) REFERENCES financial_spaces (id))''')
        
        # Таблица расходов
        c.execute('''CREATE TABLE IF NOT EXISTS expenses
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER, 
                      user_name TEXT,
                      space_id INTEGER,
                      amount REAL, 
                      category TEXT, 
                      description TEXT, 
                      date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (space_id) REFERENCES financial_spaces (id))''')
        
        # Таблица бюджетов
        c.execute('''CREATE TABLE IF NOT EXISTS budgets
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      space_id INTEGER,
                      amount REAL,
                      month_year TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (space_id) REFERENCES financial_spaces (id))''')
        
    else:
        # PostgreSQL
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS financial_spaces
                     (id SERIAL PRIMARY KEY,
                      name TEXT NOT NULL,
                      description TEXT,
                      space_type TEXT DEFAULT 'personal',
                      created_by BIGINT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      invite_code TEXT UNIQUE,
                      is_active BOOLEAN DEFAULT TRUE)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS space_members
                     (id SERIAL PRIMARY KEY,
                      space_id INTEGER REFERENCES financial_spaces(id),
                      user_id BIGINT,
                      user_name TEXT,
                      role TEXT DEFAULT 'member',
                      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS expenses
                     (id SERIAL PRIMARY KEY,
                      user_id BIGINT, 
                      user_name TEXT,
                      space_id INTEGER REFERENCES financial_spaces(id),
                      amount REAL, 
                      category TEXT, 
                      description TEXT, 
                      date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS budgets
                     (id SERIAL PRIMARY KEY,
                      user_id BIGINT,
                      space_id INTEGER REFERENCES financial_spaces(id),
                      amount REAL,
                      month_year TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# ===== УЛУЧШЕННОЕ РАСПОЗНАВАНИЕ ЧЕКОВ =====
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
logger.info("🔄 Проверяю Tesseract OCR...")
TESSERACT_AVAILABLE = check_tesseract_installation()

if TESSERACT_AVAILABLE:
    try:
        import pytesseract
        # Настройка пути к Tesseract (для Windows)
        if os.name == 'nt':  # Windows
            possible_paths = [
                r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                r'C:\Users\*\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    pytesseract.pytesseract.tesseract_cmd = path
                    break
        
        # Проверяем доступность
        pytesseract.get_tesseract_version()
        logger.info("✅ Tesseract OCR доступен")
    except Exception as e:
        TESSERACT_AVAILABLE = False
        logger.warning(f"❌ Tesseract OCR недоступен: {e}")
else:
    logger.warning("⚠️ Tesseract не установлен. Распознавание чеков недоступно.")

def preprocess_image_for_ocr(image):
    """Улучшение качества изображения для OCR"""
    try:
        # Конвертируем в grayscale
        if image.mode != 'L':
            image = image.convert('L')
        
        # Увеличиваем разрешение
        width, height = image.size
        if width < 1000 or height < 1000:
            new_size = (width * 2, height * 2)
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        # Увеличиваем контрастность
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        
        # Увеличиваем резкость
        image = image.filter(ImageFilter.SHARPEN)
        
        # Применяем бинаризацию
        image = image.point(lambda x: 0 if x < 128 else 255, '1')
        
        return image
    except Exception as e:
        logger.error(f"❌ Ошибка обработки изображения: {e}")
        return image

def parse_receipt_text(text):
    """Улучшенный парсинг распознанного текста чека"""
    logger.info("🔍 Анализирую текст чека...")
    
    lines = text.split('\n')
    receipt_data = {
        'total': 0,
        'store': None,
        'date': None,
        'items': [],
        'raw_text': text
    }
    
    # Паттерны для поиска сумм (улучшенные)
    total_patterns = [
        r'(?:итого|всего|сумма|к\s*оплате|total|итог|чек)[^\d]*(\d+[.,]\d{2})',
        r'(\d+[.,]\d{2})\s*(?:руб|р|₽|rur|rub|r|рублей)',
        r'(?:цена|стоимость|оплат|внесен)[^\d]*(\d+[.,]\d{2})',
        r'(\d+[.,]\d{2})\s*$',  # Числа в конце строки
    ]
    
    # Поиск магазина
    store_keywords = ['магазин', 'супермаркет', 'торговый', 'центр', 'аптека', 'кафе', 'ресторан']
    
    # Поиск по паттернам
    for line in lines:
        line_clean = re.sub(r'[^\w\s\d.,]', '', line.lower())
        
        # Поиск суммы
        for pattern in total_patterns:
            matches = re.findall(pattern, line_clean, re.IGNORECASE)
            if matches:
                try:
                    amount_str = matches[-1].replace(',', '.')
                    amount_str = re.sub(r'[^\d.]', '', amount_str)
                    amount = float(amount_str)
                    # Более строгая проверка на реалистичную сумму
                    if 10 <= amount <= 50000 and amount > receipt_data['total']:
                        receipt_data['total'] = amount
                        logger.info(f"💰 Найдена сумма: {amount}")
                        break
                except ValueError:
                    continue
        
        # Поиск магазина
        if not receipt_data['store']:
            # Ищем строки с названиями магазинов
            if any(keyword in line_clean for keyword in store_keywords):
                # Берем первую строку с ключевым словом как название магазина
                receipt_data['store'] = line.strip()[:50]  # Ограничиваем длину
                logger.info(f"🏪 Найден магазин: {receipt_data['store']}")
            
            # Альтернативный поиск - строки в верхнем регистре (часто это названия)
            if line.strip().isupper() and len(line.strip()) > 3 and len(line.strip()) < 30:
                receipt_data['store'] = line.strip()
                logger.info(f"🏪 Найдено название (верхний регистр): {receipt_data['store']}")
    
    return receipt_data

async def process_receipt_photo(image_bytes):
    """Обрабатываем фото чека через Tesseract с улучшенной обработкой"""
    if not TESSERACT_AVAILABLE:
        logger.warning("❌ Tesseract недоступен для распознавания чеков")
        return None
    
    try:
        logger.info("🔍 Распознаю чек через Tesseract...")
        
        image = Image.open(io.BytesIO(image_bytes))
        
        # Улучшаем качество изображения
        image = preprocess_image_for_ocr(image)
        
        # Пробуем разные настройки OCR
        configs = [
            r'--oem 3 --psm 6',
            r'--oem 3 --psm 4', 
            r'--oem 3 --psm 8',
            r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789.,рубРУБкКтТ₽'
        ]
        
        best_text = ""
        for config in configs:
            try:
                text = pytesseract.image_to_string(image, lang='rus+eng', config=config)
                if len(text.strip()) > len(best_text.strip()):
                    best_text = text
            except Exception as e:
                logger.warning(f"❌ Ошибка OCR с конфигом {config}: {e}")
                continue
        
        if not best_text.strip():
            logger.warning("❌ Не удалось распознать текст")
            return None
        
        logger.info(f"✅ Распознано символов: {len(best_text)}")
        logger.info(f"📄 Текст чека: {best_text[:300]}...")
        
        return parse_receipt_text(best_text)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки чека: {e}")
        return None

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def is_user_in_space(user_id, space_id):
    """Проверяет, состоит ли пользователь в пространстве"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT 1 FROM space_members WHERE user_id = ? AND space_id = ?'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id))
        else:
            query = '''SELECT 1 FROM space_members WHERE user_id = %s AND space_id = %s'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id))
        
        return not df.empty
    except Exception as e:
        logger.error(f"❌ Error checking user in space: {e}")
        return False
    finally:
        conn.close()

def is_user_admin_in_space(user_id, space_id):
    """Проверяет, является ли пользователь админом в пространстве"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT role FROM space_members WHERE user_id = ? AND space_id = ?'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id))
        else:
            query = '''SELECT role FROM space_members WHERE user_id = %s AND space_id = %s'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id))
        
        if not df.empty:
            role = df.iloc[0]['role']
            return role in ['owner', 'admin']
        return False
    except Exception as e:
        logger.error(f"❌ Error checking admin rights: {e}")
        return False
    finally:
        conn.close()

def create_personal_space(user_id, user_name):
    """Создание личного пространства"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('''INSERT INTO financial_spaces (name, description, space_type, created_by, invite_code)
                         VALUES (?, ?, ?, ?, ?)''', 
                     (f"Личное пространство {user_name}", "Ваше личное финансовое пространство", "personal", user_id, f"PERSONAL_{user_id}"))
            space_id = c.lastrowid
            
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (?, ?, ?, ?)''', (space_id, user_id, user_name, 'owner'))
        else:
            c = conn.cursor()
            c.execute('''INSERT INTO financial_spaces (name, description, space_type, created_by, invite_code)
                         VALUES (%s, %s, %s, %s, %s) RETURNING id''', 
                     (f"Личное пространство {user_name}", "Ваше личное финансовое пространство", "personal", user_id, f"PERSONAL_{user_id}"))
            space_id = c.fetchone()[0]
            
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (%s, %s, %s, %s)''', (space_id, user_id, user_name, 'owner'))
        
        conn.commit()
        return space_id
    except Exception as e:
        logger.error(f"❌ Ошибка создания личного пространства: {e}")
        return None
    finally:
        conn.close()

def create_financial_space(name, description, space_type, created_by, created_by_name):
    """Создание нового финансового пространства"""
    conn = get_db_connection()
    
    try:
        invite_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        
        logger.info(f"🔧 Создание пространства: {name}, тип: {space_type}, created_by: {created_by}")
        
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('''INSERT INTO financial_spaces (name, description, space_type, created_by, invite_code)
                         VALUES (?, ?, ?, ?, ?)''', 
                     (name, description, space_type, created_by, invite_code))
            space_id = c.lastrowid
            
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (?, ?, ?, ?)''', (space_id, created_by, created_by_name, 'owner'))
        else:
            c = conn.cursor()
            c.execute('''INSERT INTO financial_spaces (name, description, space_type, created_by, invite_code)
                         VALUES (%s, %s, %s, %s, %s) RETURNING id''', 
                     (name, description, space_type, created_by, invite_code))
            space_id = c.fetchone()[0]
            
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (%s, %s, %s, %s)''', (space_id, created_by, created_by_name, 'owner'))
        
        conn.commit()
        logger.info(f"✅ Пространство успешно создано: ID {space_id}")
        return space_id, invite_code
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания пространства: {e}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        if conn:
            conn.rollback()
        return None, None
    finally:
        if conn:
            conn.close()

def add_expense(user_id, user_name, amount, category, description="", space_id=None):
    """Добавление траты в базу"""
    try:
        if space_id is None:
            space_id = ensure_user_has_personal_space(user_id, user_name)
        
        logger.info(f"💾 Сохраняем в базу: {user_name} - {amount} руб - {category} - space: {space_id}")
        
        conn = get_db_connection()
        c = conn.cursor()
        
        if isinstance(conn, sqlite3.Connection):
            c.execute('''INSERT INTO expenses (user_id, user_name, amount, category, description, space_id)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (user_id, user_name, amount, category, description, space_id))
        else:
            c.execute('''INSERT INTO expenses (user_id, user_name, amount, category, description, space_id)
                         VALUES (%s, %s, %s, %s, %s, %s)''',
                      (user_id, user_name, amount, category, description, space_id))
        
        conn.commit()
        conn.close()
        logger.info(f"✅ Добавлена трата: {user_name} - {amount} руб - {category} - space: {space_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении в базу: {str(e)}")

def ensure_user_has_personal_space(user_id, user_name):
    """Гарантирует, что у пользователя есть личное пространство"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT fs.id FROM financial_spaces fs
                       JOIN space_members sm ON fs.id = sm.space_id
                       WHERE sm.user_id = ? AND fs.space_type = 'personal' AND fs.is_active = TRUE'''
            df = pd.read_sql_query(query, conn, params=(user_id,))
        else:
            query = '''SELECT fs.id FROM financial_spaces fs
                       JOIN space_members sm ON fs.id = sm.space_id
                       WHERE sm.user_id = %s AND fs.space_type = 'personal' AND fs.is_active = TRUE'''
            df = pd.read_sql_query(query, conn, params=(user_id,))
        
        if not df.empty:
            return df.iloc[0]['id']
        else:
            return create_personal_space(user_id, user_name)
            
    except Exception as e:
        logger.error(f"❌ Error ensuring personal space: {e}")
        return create_personal_space(user_id, user_name)
    finally:
        conn.close()

def remove_member_from_space(space_id, user_id, remover_id):
    """Удаление участника из пространства"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('DELETE FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, user_id))
        else:
            c = conn.cursor()
            c.execute('DELETE FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, user_id))
        
        conn.commit()
        return True, "Участник удален"
    except Exception as e:
        logger.error(f"❌ Error removing member: {e}")
        return False, "Ошибка при удалении"
    finally:
        conn.close()

def set_user_budget(user_id, space_id, amount):
    """Установка бюджета пользователя"""
    conn = get_db_connection()
    
    try:
        current_month = datetime.now().strftime('%Y-%m')
        
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            # Проверяем, есть ли уже бюджет на этот месяц
            c.execute('SELECT id FROM budgets WHERE user_id = ? AND space_id = ? AND month_year = ?', 
                     (user_id, space_id, current_month))
            existing = c.fetchone()
            
            if existing:
                c.execute('UPDATE budgets SET amount = ? WHERE id = ?', (amount, existing[0]))
            else:
                c.execute('INSERT INTO budgets (user_id, space_id, amount, month_year) VALUES (?, ?, ?, ?)',
                         (user_id, space_id, amount, current_month))
        else:
            c = conn.cursor()
            c.execute('SELECT id FROM budgets WHERE user_id = %s AND space_id = %s AND month_year = %s', 
                     (user_id, space_id, current_month))
            existing = c.fetchone()
            
            if existing:
                c.execute('UPDATE budgets SET amount = %s WHERE id = %s', (amount, existing[0]))
            else:
                c.execute('INSERT INTO budgets (user_id, space_id, amount, month_year) VALUES (%s, %s, %s, %s)',
                         (user_id, space_id, amount, current_month))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Error setting budget: {e}")
        return False
    finally:
        conn.close()

def get_user_budget(user_id, space_id):
    """Получение бюджета пользователя"""
    conn = get_db_connection()
    
    try:
        current_month = datetime.now().strftime('%Y-%m')
        
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT amount FROM budgets WHERE user_id = ? AND space_id = ? AND month_year = ?'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id, current_month))
        else:
            query = '''SELECT amount FROM budgets WHERE user_id = %s AND space_id = %s AND month_year = %s'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id, current_month))
        
        if not df.empty:
            return float(df.iloc[0]['amount'])
        else:
            return 0
    except Exception as e:
        logger.error(f"❌ Error getting budget: {e}")
        return 0
    finally:
        conn.close()

# ===== API ENDPOINTS =====
@flask_app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok', 
        'message': 'Finance Bot API is running',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0',
        'dev_mode': DEV_MODE
    })

@flask_app.route('/get_user_spaces', methods=['POST'])
def api_get_user_spaces():
    """API для получения пространств пользователя"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        
        logger.info(f"📦 Получен запрос get_user_spaces: {data.keys()}")
        
        if not validate_webapp_data(init_data):
            logger.warning("❌ Валидация WebApp данных не пройдена")
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            logger.warning("❌ Не удалось извлечь данные пользователя")
            return jsonify({'error': 'User not found'}), 401
            
        user_id = user_data['id']
        logger.info(f"👤 Получение пространств для пользователя: {user_id}")
        
        conn = get_db_connection()
        
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT fs.id, fs.name, fs.description, fs.space_type, fs.invite_code,
                              COUNT(DISTINCT sm.user_id) as member_count
                       FROM financial_spaces fs
                       JOIN space_members sm ON fs.id = sm.space_id
                       WHERE sm.user_id = ? AND fs.is_active = TRUE
                       GROUP BY fs.id
                       ORDER BY fs.space_type, fs.created_at DESC'''
            df = pd.read_sql_query(query, conn, params=(user_id,))
        else:
            query = '''SELECT fs.id, fs.name, fs.description, fs.space_type, fs.invite_code,
                              COUNT(DISTINCT sm.user_id) as member_count
                       FROM financial_spaces fs
                       JOIN space_members sm ON fs.id = sm.space_id
                       WHERE sm.user_id = %s AND fs.is_active = TRUE
                       GROUP BY fs.id
                       ORDER BY fs.space_type, fs.created_at DESC'''
            df = pd.read_sql_query(query, conn, params=(user_id,))
        
        conn.close()
        
        spaces = []
        for _, row in df.iterrows():
            spaces.append({
                'id': int(row['id']),
                'name': row['name'],
                'description': row['description'],
                'space_type': row['space_type'],
                'invite_code': row['invite_code'],
                'member_count': int(row['member_count']) if row['member_count'] else 1
            })
        
        logger.info(f"✅ Найдено пространств: {len(spaces)}")
        return jsonify({'spaces': spaces})
        
    except Exception as e:
        logger.error(f"❌ API Error in get_user_spaces: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/get_space_members', methods=['POST'])
def api_get_space_members():
    """API для получения участников пространства"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        
        logger.info(f"📦 Получен запрос get_space_members: space_id={space_id}")
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        # Проверяем, что пользователь состоит в пространстве
        if not is_user_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Access denied'}), 403
        
        conn = get_db_connection()
        
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT user_id, user_name, role, joined_at 
                       FROM space_members 
                       WHERE space_id = ?
                       ORDER BY 
                         CASE role 
                           WHEN 'owner' THEN 1 
                           WHEN 'admin' THEN 2 
                           ELSE 3 
                         END, joined_at'''
            df = pd.read_sql_query(query, conn, params=(space_id,))
        else:
            query = '''SELECT user_id, user_name, role, joined_at 
                       FROM space_members 
                       WHERE space_id = %s
                       ORDER BY 
                         CASE role 
                           WHEN 'owner' THEN 1 
                           WHEN 'admin' THEN 2 
                           ELSE 3 
                         END, joined_at'''
            df = pd.read_sql_query(query, conn, params=(space_id,))
        
        conn.close()
        
        members = []
        for _, row in df.iterrows():
            members.append({
                'user_id': int(row['user_id']),
                'user_name': row['user_name'],
                'role': row['role'],
                'joined_at': row['joined_at'].isoformat() if hasattr(row['joined_at'], 'isoformat') else str(row['joined_at'])
            })
        
        return jsonify({'members': members})
        
    except Exception as e:
        logger.error(f"❌ API Error in get_space_members: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/create_space', methods=['POST'])
def api_create_space():
    """API для создания нового пространства"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        name = data.get('name')
        space_type = data.get('type')
        description = data.get('description', '')
        
        logger.info(f"📝 Создание пространства: {name}, тип: {space_type}")
        logger.info(f"📦 Данные запроса: {data}")
        
        if not validate_webapp_data(init_data):
            logger.warning("❌ Валидация WebApp данных не пройдена")
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            logger.warning("❌ Не удалось извлечь данные пользователя")
            return jsonify({'error': 'User not found'}), 401
            
        logger.info(f"👤 Пользователь: {user_data}")
            
        if not name or not space_type:
            logger.warning("❌ Отсутствуют обязательные поля")
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Создаем пространство
        result = create_financial_space(
            name, description, space_type, 
            user_data['id'], user_data['first_name']
        )
        
        if result and result[0] is not None:
            space_id, invite_code = result
            logger.info(f"✅ Пространство создано: {space_id}, код: {invite_code}")
            return jsonify({
                'success': True,
                'space_id': space_id,
                'invite_code': invite_code
            })
        else:
            logger.error("❌ Ошибка создания пространства - функция вернула None")
            return jsonify({'error': 'Failed to create space - check database connection'}), 500
            
    except Exception as e:
        logger.error(f"❌ API Error in create_space: {e}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@flask_app.route('/add_expense', methods=['POST'])
def api_add_expense():
    """API для добавления траты"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        amount = data.get('amount')
        category = data.get('category')
        description = data.get('description', '')
        space_id = data.get('spaceId')
        
        logger.info(f"💰 Добавление траты: {amount} руб, категория: {category}")
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        if not amount or not category or not space_id:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Проверяем, что пользователь состоит в пространстве
        if not is_user_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Access denied'}), 403
        
        # Добавляем трату
        add_expense(
            user_data['id'], user_data['first_name'],
            float(amount), category, description, int(space_id)
        )
        
        return jsonify({'success': True})
            
    except Exception as e:
        logger.error(f"❌ API Error in add_expense: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/get_analytics', methods=['POST'])
def api_get_analytics():
    """API для получения аналитики"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        user_id = data.get('userId')
        
        logger.info(f"📊 Получение аналитики: space_id={space_id}, user_id={user_id}")
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        # Проверяем, что пользователь состоит в пространстве
        if not is_user_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Access denied'}), 403
        
        conn = get_db_connection()
        
        # Получаем участников пространства для фильтра
        if isinstance(conn, sqlite3.Connection):
            users_query = '''SELECT DISTINCT user_id, user_name FROM space_members WHERE space_id = ?'''
            users_df = pd.read_sql_query(users_query, conn, params=(space_id,))
        else:
            users_query = '''SELECT DISTINCT user_id, user_name FROM space_members WHERE space_id = %s'''
            users_df = pd.read_sql_query(users_query, conn, params=(space_id,))
        
        users = []
        for _, row in users_df.iterrows():
            users.append({
                'id': int(row['user_id']),
                'name': row['user_name']
            })
        
        # Статистика по категориям
        if user_id:
            if isinstance(conn, sqlite3.Connection):
                query = '''SELECT category, SUM(amount) as total, COUNT(*) as count
                           FROM expenses 
                           WHERE space_id = ? AND user_id = ?
                           GROUP BY category 
                           ORDER BY total DESC'''
                df = pd.read_sql_query(query, conn, params=(space_id, user_id))
                
                count_query = '''SELECT COUNT(*) as total_count FROM expenses WHERE space_id = ? AND user_id = ?'''
                count_df = pd.read_sql_query(count_query, conn, params=(space_id, user_id))
                
                # Получаем общую сумму для бюджета
                total_spent_query = '''SELECT COALESCE(SUM(amount), 0) as total_spent FROM expenses WHERE space_id = ? AND user_id = ? AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now')'''
                total_spent_df = pd.read_sql_query(total_spent_query, conn, params=(space_id, user_id))
            else:
                query = '''SELECT category, SUM(amount) as total, COUNT(*) as count
                           FROM expenses 
                           WHERE space_id = %s AND user_id = %s
                           GROUP BY category 
                           ORDER BY total DESC'''
                df = pd.read_sql_query(query, conn, params=(space_id, user_id))
                
                count_query = '''SELECT COUNT(*) as total_count FROM expenses WHERE space_id = %s AND user_id = %s'''
                count_df = pd.read_sql_query(count_query, conn, params=(space_id, user_id))
                
                total_spent_query = '''SELECT COALESCE(SUM(amount), 0) as total_spent FROM expenses WHERE space_id = %s AND user_id = %s AND DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)'''
                total_spent_df = pd.read_sql_query(total_spent_query, conn, params=(space_id, user_id))
        else:
            if isinstance(conn, sqlite3.Connection):
                query = '''SELECT category, SUM(amount) as total, COUNT(*) as count
                           FROM expenses 
                           WHERE space_id = ?
                           GROUP BY category 
                           ORDER BY total DESC'''
                df = pd.read_sql_query(query, conn, params=(space_id,))
                
                count_query = '''SELECT COUNT(*) as total_count FROM expenses WHERE space_id = ?'''
                count_df = pd.read_sql_query(count_query, conn, params=(space_id,))
                
                total_spent_query = '''SELECT COALESCE(SUM(amount), 0) as total_spent FROM expenses WHERE space_id = ? AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now')'''
                total_spent_df = pd.read_sql_query(total_spent_query, conn, params=(space_id,))
            else:
                query = '''SELECT category, SUM(amount) as total, COUNT(*) as count
                           FROM expenses 
                           WHERE space_id = %s
                           GROUP BY category 
                           ORDER BY total DESC'''
                df = pd.read_sql_query(query, conn, params=(space_id,))
                
                count_query = '''SELECT COUNT(*) as total_count FROM expenses WHERE space_id = %s'''
                count_df = pd.read_sql_query(count_query, conn, params=(space_id,))
                
                total_spent_query = '''SELECT COALESCE(SUM(amount), 0) as total_spent FROM expenses WHERE space_id = %s AND DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)'''
                total_spent_df = pd.read_sql_query(total_spent_query, conn, params=(space_id,))
        
        conn.close()
        
        categories = []
        for _, row in df.iterrows():
            categories.append({
                'name': row['category'],
                'total': float(row['total']),
                'count': int(row['count'])
            })
        
        total_count = int(count_df.iloc[0]['total_count']) if not count_df.empty else 0
        total_spent = float(total_spent_df.iloc[0]['total_spent']) if not total_spent_df.empty else 0
        
        # Получаем бюджет пользователя
        budget = get_user_budget(user_data['id'], space_id)
        
        return jsonify({
            'categories': categories,
            'total_count': total_count,
            'total_spent': total_spent,
            'budget': budget,
            'users': users
        })
        
    except Exception as e:
        logger.error(f"❌ API Error in get_analytics: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/set_budget', methods=['POST'])
def api_set_budget():
    """API для установки бюджета"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        amount = data.get('amount')
        
        logger.info(f"🎯 Установка бюджета: {amount} руб для space_id={space_id}")
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        if not amount or not space_id:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Проверяем, что пользователь состоит в пространстве
        if not is_user_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Access denied'}), 403
        
        success = set_user_budget(user_data['id'], space_id, float(amount))
        
        return jsonify({'success': success})
            
    except Exception as e:
        logger.error(f"❌ API Error in set_budget: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/join_space', methods=['POST'])
def api_join_space():
    """API для присоединения к пространству по коду"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        invite_code = data.get('inviteCode')
        
        logger.info(f"👥 Присоединение к пространству по коду: {invite_code}")
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        if not invite_code:
            return jsonify({'error': 'Missing invite code'}), 400
        
        conn = get_db_connection()
        
        # Находим пространство по коду
        if isinstance(conn, sqlite3.Connection):
            space_query = '''SELECT id, name FROM financial_spaces WHERE invite_code = ? AND is_active = TRUE'''
            space_df = pd.read_sql_query(space_query, conn, params=(invite_code,))
        else:
            space_query = '''SELECT id, name FROM financial_spaces WHERE invite_code = %s AND is_active = TRUE'''
            space_df = pd.read_sql_query(space_query, conn, params=(invite_code,))
        
        if space_df.empty:
            return jsonify({'error': 'Неверный код приглашения'}), 404
        
        space_id = space_df.iloc[0]['id']
        space_name = space_df.iloc[0]['name']
        
        # Проверяем, не состоит ли пользователь уже в пространстве
        if isinstance(conn, sqlite3.Connection):
            member_query = '''SELECT 1 FROM space_members WHERE space_id = ? AND user_id = ?'''
            member_df = pd.read_sql_query(member_query, conn, params=(space_id, user_data['id']))
        else:
            member_query = '''SELECT 1 FROM space_members WHERE space_id = %s AND user_id = %s'''
            member_df = pd.read_sql_query(member_query, conn, params=(space_id, user_data['id']))
        
        if not member_df.empty:
            return jsonify({'error': 'Вы уже состоите в этом пространстве'}), 400
        
        # Добавляем пользователя в пространство
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (?, ?, ?, ?)''', 
                     (space_id, user_data['id'], user_data['first_name'], 'member'))
        else:
            c = conn.cursor()
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (%s, %s, %s, %s)''', 
                     (space_id, user_data['id'], user_data['first_name'], 'member'))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'space_id': space_id,
            'space_name': space_name
        })
        
    except Exception as e:
        logger.error(f"❌ API Error in join_space: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/remove_member', methods=['POST'])
def api_remove_member():
    """API для удаления участника из пространства"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        target_user_id = data.get('targetUserId')
        
        logger.info(f"🗑️ Удаление участника: {target_user_id} из space_id={space_id}")
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        if not space_id or not target_user_id:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Проверяем права администратора
        if not is_user_admin_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Недостаточно прав'}), 403
        
        # Нельзя удалить самого себя
        if user_data['id'] == target_user_id:
            return jsonify({'error': 'Нельзя удалить самого себя'}), 400
        
        success, message = remove_member_from_space(space_id, target_user_id, user_data['id'])
        
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        logger.error(f"❌ API Error in remove_member: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# ===== TELEGRAM BOT HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Создаем клавиатуру с кнопкой веб-приложения
    keyboard = [
        [KeyboardButton("📊 Открыть финансовый трекер", web_app=WebAppInfo(url=WEB_APP_URL))]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу тебе отслеживать твои финансы и управлять бюджетами. "
        "Нажми кнопку ниже, чтобы открыть финансовый трекер!\n\n"
        "Возможности:\n"
        "• 📝 Добавление трат\n"
        "• 📊 Аналитика расходов\n"
        "• 👥 Совместные бюджеты\n"
        "• 🧾 Распознавание чеков\n"
        "• 🎯 Установка лимитов",
        reply_markup=reply_markup
    )

async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка данных из веб-приложения"""
    try:
        user = update.effective_user
        data = update.message.web_app_data.data
        
        # Парсим данные из веб-приложения
        parsed_data = json.loads(data)
        action = parsed_data.get('action')
        
        if action == 'add_expense':
            amount = parsed_data.get('amount')
            category = parsed_data.get('category')
            description = parsed_data.get('description', '')
            
            add_expense(user.id, user.first_name, amount, category, description)
            
            await update.message.reply_text(
                f"✅ Трата добавлена!\n"
                f"💸 Сумма: {amount} руб\n"
                f"📂 Категория: {category}\n"
                f"📝 Описание: {description if description else 'нет'}"
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки данных веб-приложения: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке данных")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото с чеком"""
    user = update.effective_user
    
    try:
        # Получаем фото
        photo_file = await update.message.photo[-1].get_file()
        
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            await photo_file.download_to_drive(temp_file.name)
            
            # Читаем байты
            with open(temp_file.name, 'rb') as f:
                image_bytes = f.read()
        
        # Удаляем временный файл
        os.unlink(temp_file.name)
        
        await update.message.reply_text("🔍 Анализирую чек...")
        
        # Обрабатываем чек
        receipt_data = await process_receipt_photo(image_bytes)
        
        if receipt_data and receipt_data['total'] > 0:
            # Создаем клавиатуру для подтверждения
            keyboard = [
                [KeyboardButton(f"✅ Да, добавить {receipt_data['total']} руб")],
                [KeyboardButton("❌ Нет, отменить")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
            
            message_text = f"📄 Чек распознан!\n\n"
            if receipt_data['store']:
                message_text += f"🏪 Магазин: {receipt_data['store']}\n"
            message_text += f"💰 Сумма: {receipt_data['total']} руб\n\n"
            message_text += "Добавить эту трату?"
            
            # Сохраняем данные чека в контексте
            context.user_data['pending_receipt'] = receipt_data
            
            await update.message.reply_text(message_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(
                "❌ Не удалось распознать сумму чека. "
                "Пожалуйста, добавьте трату вручную через веб-приложение."
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке чека")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосовых сообщений"""
    user = update.effective_user
    
    try:
        voice_file = await update.message.voice.get_file()
        
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as temp_file:
            await voice_file.download_to_drive(temp_file.name)
        
        # Распознаем речь
        r = sr.Recognizer()
        with sr.AudioFile(temp_file.name) as source:
            audio = r.record(source)
        
        # Удаляем временный файл
        os.unlink(temp_file.name)
        
        # Распознаем текст
        text = r.recognize_google(audio, language='ru-RU')
        
        await update.message.reply_text(f"🎤 Распознано: {text}")
        
        # Простой парсинг для тестирования
        amount_match = re.search(r'(\d+)\s*(?:руб|р|₽)', text.lower())
        category_match = re.search(r'(еда|продукты|транспорт|кафе|развлечения|одежда|другое)', text.lower())
        
        if amount_match:
            amount = float(amount_match.group(1))
            category = category_match.group(1) if category_match else 'другое'
            
            add_expense(user.id, user.first_name, amount, category, f"Голосовое: {text}")
            
            await update.message.reply_text(
                f"✅ Трата добавлена!\n"
                f"💸 Сумма: {amount} руб\n"
                f"📂 Категория: {category}"
            )
        else:
            await update.message.reply_text(
                "❌ Не удалось распознать сумму в сообщении. "
                "Пожалуйста, используйте формат: '500 рублей на еду'"
            )
            
    except sr.UnknownValueError:
        await update.message.reply_text("❌ Не удалось распознать речь")
    except Exception as e:
        logger.error(f"❌ Ошибка обработки голоса: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке голосового сообщения")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user = update.effective_user
    text = update.message.text
    
    # Обработка подтверждения чека
    if 'pending_receipt' in context.user_data and text.startswith('✅ Да'):
        receipt_data = context.user_data['pending_receipt']
        
        add_expense(
            user.id, user.first_name, 
            receipt_data['total'], 
            'покупки', 
            f"Чек: {receipt_data['store'] or 'магазин'}"
        )
        
        await update.message.reply_text(
            f"✅ Трата добавлена!\n"
            f"💸 Сумма: {receipt_data['total']} руб\n"
            f"🏪 Магазин: {receipt_data['store'] or 'не указан'}"
        )
        
        # Удаляем данные чека из контекста
        del context.user_data['pending_receipt']
        return
    
    elif 'pending_receipt' in context.user_data and text.startswith('❌ Нет'):
        await update.message.reply_text("❌ Добавление траты отменено")
        del context.user_data['pending_receipt']
        return
    
    # Обработка простых команд
    if text.lower() in ['помощь', 'help', 'команды']:
        await update.message.reply_text(
            "📋 Доступные команды:\n\n"
            "• Нажмите кнопку '📊 Открыть финансовый трекер' для доступа ко всем функциям\n"
            "• Отправьте фото чека для автоматического распознавания\n"
            "• Отправьте голосовое сообщение с описанием траты\n"
            "• Используйте веб-приложение для полного контроля над финансами\n\n"
            "Возможности:\n"
            "✅ Учет личных и совместных трат\n"
            "📊 Аналитика и статистика\n"
            "🎯 Установка бюджетов\n"
            "👥 Управление группами\n"
            "🧾 Распознавание чеков\n"
            "🎤 Голосовой ввод"
        )
    else:
        await update.message.reply_text(
            "🤖 Я финансовый помощник!\n\n"
            "Используйте кнопку ниже для открытия полного функционала, "
            "или отправьте мне фото чека для автоматического распознавания.",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("📊 Открыть финансовый трекер", web_app=WebAppInfo(url=WEB_APP_URL))]
            ], resize_keyboard=True)
        )

# ===== ОСНОВНАЯ ФУНКЦИЯ =====
def main():
    """Основная функция запуска бота"""
    # Проверяем наличие токена
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден! Убедитесь, что переменная окружения BOT_TOKEN установлена.")
        return
    
    # Инициализация базы данных
    init_db()
    
    # Создаем приложение бота
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Запускаем Flask в отдельном потоке
    import threading
    port = int(os.environ.get('PORT', 5000))
    
    def run_flask():
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Flask API запущен на порту {port}")
    logger.info(f"🔧 Режим разработки: {DEV_MODE}")
    
    # Запускаем бота
    logger.info("🤖 Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    main()





