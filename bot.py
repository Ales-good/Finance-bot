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
import threading
import asyncio

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app для API
flask_app = Flask(__name__)

# Глобальные переменные
bot_instance = None
application = None

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

# ===== ВАЛИДАЦИЯ WEBAPP DATA =====
def validate_webapp_data(init_data):
    """Валидация данных от Telegram WebApp"""
    try:
        if not init_data:
            return False
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка валидации WebApp данных: {e}")
        return False

def get_user_from_init_data(init_data):
    """Извлечение данных пользователя из initData"""
    try:
        params = {}
        for item in init_data.split('&'):
            if '=' in item:
                key, value = item.split('=', 1)
                params[key] = value
        
        if 'user' in params:
            user_data = json.loads(params['user'])
            return {
                'id': user_data.get('id'),
                'first_name': user_data.get('first_name'),
                'username': user_data.get('username')
            }
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга initData: {e}")
    
    return None

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
                logger.info(f"🏪 Найден магазин: {rece_data['store']}")
            
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

# ===== API ENDPOINTS =====
@flask_app.route('/get_user_spaces', methods=['POST'])
def api_get_user_spaces():
    """API для получения пространств пользователя"""
    try:
        data = request.json
        init_data = data.get('initData')
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        user_id = user_data['id']
        
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
        
        return jsonify({'spaces': spaces})
        
    except Exception as e:
        logger.error(f"❌ API Error in get_user_spaces: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/get_space_members', methods=['POST'])
def api_get_space_members():
    """API для получения участников пространства"""
    try:
        data = request.json
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        
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
        init_data = data.get('initData')
        name = data.get('name')
        space_type = data.get('type')
        description = data.get('description', '')
        
        logger.info(f"📝 Создание пространства: {name}, тип: {space_type}")
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        if not name or not space_type:
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
        init_data = data.get('initData')
        amount = data.get('amount')
        category = data.get('category')
        description = data.get('description', '')
        space_id = data.get('spaceId')
        
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
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        user_id = data.get('userId')
        
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
                'category': row['category'],
                'total': float(row['total']),
                'count': int(row['count'])
            })
        
        total_spent = float(total_spent_df.iloc[0]['total_spent']) if not total_spent_df.empty else 0
        
        return jsonify({
            'categories': categories,
            'total_count': int(count_df.iloc[0]['total_count']) if not count_df.empty else 0,
            'users': users,
            'total_spent': total_spent
        })
            
    except Exception as e:
        logger.error(f"❌ API Error in get_analytics: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/remove_member', methods=['POST'])
def api_remove_member():
    """API для удаления участника"""
    try:
        data = request.json
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        member_id = data.get('memberId')
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
        
        # Проверяем права
        if not is_user_admin_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Access denied'}), 403
        
        # Удаляем участника
        success, message = remove_member_from_space(space_id, member_id, user_data['id'])
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': message}), 400
            
    except Exception as e:
        logger.error(f"❌ API Error in remove_member: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/set_budget', methods=['POST'])
def api_set_budget():
    """API для установки бюджета"""
    try:
        data = request.json
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        amount = data.get('amount')
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        if not amount or amount <= 0:
            return jsonify({'error': 'Invalid budget amount'}), 400
        
        # Проверяем, что пользователь состоит в пространстве
        if not is_user_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Access denied'}), 403
        
        # Сохраняем бюджет
        success = set_user_budget(user_data['id'], space_id, float(amount))
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Failed to set budget'}), 500
            
    except Exception as e:
        logger.error(f"❌ API Error in set_budget: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@flask_app.route('/get_budget', methods=['POST'])
def api_get_budget():
    """API для получения бюджета"""
    try:
        data = request.json
        init_data = data.get('initData')
        space_id = data.get('spaceId')
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        # Проверяем, что пользователь состоит в пространстве
        if not is_user_in_space(user_data['id'], space_id):
            return jsonify({'error': 'Access denied'}), 403
        
        # Получаем бюджет
        budget = get_user_budget(user_data['id'], space_id)
        
        return jsonify({
            'success': True,
            'budget': budget
        })
            
    except Exception as e:
        logger.error(f"❌ API Error in get_budget: {e}")
        return jsonify({'error': 'Internal server error'}), 500

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

# ===== ОСНОВНЫЕ ФУНКЦИИ БОТА =====
def get_main_keyboard():
    """Основная клавиатура для навигации в WebApp"""
    web_app_url = os.environ.get('WEB_APP_URL', 'https://your-app-url.com')
    
    keyboard = [
        [KeyboardButton("💸 Открыть форму", web_app=WebAppInfo(url=web_app_url))],
        [KeyboardButton("📊 Статистика"), KeyboardButton("📝 Последние траты")],
        [KeyboardButton("🆘 Помощь"), KeyboardButton("🗑️ Очистить данные")]
    ]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Гарантируем, что у пользователя есть личное пространство
    ensure_user_has_personal_space(user.id, user.first_name)
    
    welcome_text = f"""
Привет, {user.first_name}! 👋

Я бот для учета финансов 💰 с **полноценным Web-интерфейсом**!

🚀 **Основные возможности:**
• 💸 **Удобное добавление трат** через Web-форму
• 🏠 **Управление пространствами** - личные, семейные, публичные
• 👥 **Совместные бюджеты** с друзьями и семьей  
• 📊 **Детальная аналитика** с графиками
• 📸 **Распознавание чеков** и 🎤 **голосовой ввод**

💡 **Нажмите «💸 Открыть форму» для доступа ко всем функциям!**
"""
    
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрая статистика через бота"""
    try:
        user = update.effective_user
        space_id = ensure_user_has_personal_space(user.id, user.first_name)
        
        conn = get_db_connection()
        
        if isinstance(conn, sqlite3.Connection):
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total
                FROM expenses 
                WHERE user_id = {user.id} AND space_id = {space_id}
                GROUP BY category 
                ORDER BY total DESC
                LIMIT 5
            ''', conn)
        else:
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total
                FROM expenses 
                WHERE user_id = {user.id} AND space_id = {space_id}
                GROUP BY category 
                ORDER BY total DESC
                LIMIT 5
            ''', conn)
        
        conn.close()
        
        if df.empty:
            await update.message.reply_text(
                "📊 Пока нет данных для статистики.\n\n"
                "💡 Откройте Web-форму для детальной аналитики!",
                reply_markup=get_main_keyboard()
            )
            return
        
        total_spent = df['total'].sum()
        stats_text = f"📊 **Быстрая статистика**\n\n💰 **Всего потрачено:** {total_spent:,.0f} руб\n\n**Топ категории:**\n"
        
        for _, row in df.iterrows():
            percentage = (row['total'] / total_spent) * 100
            stats_text += f"• {row['category']}: {row['total']:,.0f} руб ({percentage:.1f}%)\n"
        
        stats_text += "\n💡 **Для детальной аналитики откройте Web-форму!**"
        
        await update.message.reply_text(stats_text, reply_markup=get_main_keyboard())
        
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {str(e)}")
        await update.message.reply_text(
            "❌ Ошибка при формировании статистики. Попробуйте открыть Web-форму.",
            reply_markup=get_main_keyboard()
        )

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Последние траты"""
    try:
        user = update.effective_user
        space_id = ensure_user_has_personal_space(user.id, user.first_name)
        
        conn = get_db_connection()
        
        if isinstance(conn, sqlite3.Connection):
            df = pd.read_sql_query(f'''
                SELECT amount, category, description, date
                FROM expenses 
                WHERE user_id = {user.id} AND space_id = {space_id}
                ORDER BY date DESC 
                LIMIT 5
            ''', conn)
        else:
            df = pd.read_sql_query(f'''
                SELECT amount, category, description, date
                FROM expenses 
                WHERE user_id = {user.id} AND space_id = {space_id}
                ORDER BY date DESC 
                LIMIT 5
            ''', conn)
        
        conn.close()
        
        if df.empty:
            await update.message.reply_text(
                "📝 Пока нет добавленных трат.\n\n"
                "💡 Откройте Web-форму для удобного добавления!",
                reply_markup=get_main_keyboard()
            )
            return
        
        list_text = "📝 **Последние траты:**\n\n"
        
        for _, row in df.iterrows():
            date = datetime.strptime(str(row['date']), '%Y-%m-%d %H:%M:%S').strftime('%d.%m %H:%M')
            list_text += f"💸 **{row['amount']} руб** - {row['category']}\n"
            
            if row['description']:
                list_text += f"   📋 {row['description']}\n"
            
            list_text += f"   📅 {date}\n\n"
        
        list_text += "💡 **Откройте Web-форму для полной истории!**"
        
        await update.message.reply_text(list_text, parse_mode='Markdown', reply_markup=get_main_keyboard())
        
    except Exception as e:
        logger.error(f"❌ Ошибка списка трат: {str(e)}")
        await update.message.reply_text(
            "❌ Ошибка при получении списка трат.",
            reply_markup=get_main_keyboard()
        )

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очистка данных пользователя"""
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
            "✅ Все ваши данные успешно очищены!\n"
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
🆘 **ПОМОЩЬ**

💡 **Основные команды:**
• **💸 Открыть форму** - Полноценный Web-интерфейс со всеми функциями
• **📊 Статистика** - Быстрая статистика в чате
• **📝 Последние траты** - История операций

🚀 **Что можно в Web-форме:**
• Удобное добавление трат с калькулятором
• Управление пространствами (личные, семейные, публичные)
• Приглашение участников в группы
• Детальная аналитика с графиками
• Просмотр участников и управление

🎯 **Быстрый ввод через бота:**
• Текстом: `500 продукты` или `1500 кафе обед`
• Голосовым сообщением
• Фото чека (автораспознавание)

💬 **Просто отправьте сумму и категорию текстом для быстрого добавления!**
"""
    await update.message.reply_text(help_text, reply_markup=get_main_keyboard())

# ===== ОБРАБОТЧИКИ ТЕКСТА И МЕДИА =====
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    text = update.message.text
    
    logger.info(f"📨 Текст от {user.first_name}: {text}")
    
    # Обработка кнопок главного меню
    if text == "📊 Статистика":
        await show_stats(update, context)
    elif text == "📝 Последние траты":
        await show_list(update, context)
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
                
                space_id = ensure_user_has_personal_space(user.id, user.first_name)
                add_expense(user.id, user.first_name, amount, category, description, space_id)
                
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

# ===== ОБРАБОТЧИКИ ГОЛОСА И ФОТО =====
class VoiceRecognizer:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.pause_threshold = 0.8
        self.recognizer.dynamic_energy_threshold = True
        
    async def transcribe_audio(self, audio_path):
        """Транскрибируем аудио файл"""
        try:
            with sr.AudioFile(audio_path) as source:
                audio = self.recognizer.record(source)
                text = self.recognizer.recognize_google(audio, language='ru-RU')
                return text
        except sr.UnknownValueError:
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка распознавания голоса: {e}")
            return None

voice_recognizer = VoiceRecognizer()

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений"""
    try:
        user = update.effective_user
        voice = update.message.voice
        
        processing_msg = await update.message.reply_text("🎤 Обрабатываю голосовое сообщение...")
        
        # Скачиваем голосовое сообщение
        voice_file = await voice.get_file()
        
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
            temp_path = temp_file.name
        
        try:
            # Скачиваем файл
            await voice_file.download_to_drive(temp_path)
            
            # Распознаем речь
            text = await voice_recognizer.transcribe_audio(temp_path)
            
            if not text:
                await processing_msg.edit_text(
                    "❌ Не удалось распознать голосовое сообщение.\n\n"
                    "💡 **Попробуйте:**\n"
                    "• Говорить четче и ближе к микрофону\n"
                    "• Использовать формат: '500 продукты'\n"
                    "• Или ввести трату текстом"
                )
                return
            
            await processing_msg.edit_text(f"🎤 **Распознано:** _{text}_", parse_mode='Markdown')
            
            # Парсим текст
            words = text.lower().split()
            amount = None
            category = "Другое"
            
            # Ищем сумму
            for word in words:
                cleaned_word = re.sub(r'[^\d]', '', word)
                if cleaned_word:
                    try:
                        potential_amount = int(cleaned_word)
                        if 10 <= potential_amount <= 100000:
                            amount = potential_amount
                            break
                    except:
                        pass
            
            # Определяем категорию
            category_keywords = {
                'Продукты': ['продукт', 'еда', 'магазин', 'супермаркет', 'покупк'],
                'Кафе': ['кафе', 'ресторан', 'кофе', 'обед', 'ужин'],
                'Транспорт': ['транспорт', 'такси', 'метро', 'автобус', 'бензин'],
                'Дом': ['дом', 'квартир', 'коммунал', 'аренд'],
                'Одежда': ['одежд', 'обув', 'шопинг'],
                'Здоровье': ['здоров', 'аптек', 'врач', 'лекарств'],
                'Развлечения': ['развлечен', 'кино', 'концерт', 'театр'],
                'Подписки': ['подписк', 'интернет', 'телефон'],
                'Маркетплейсы': ['wildberries', 'озон', 'яндекс маркет']
            }
            
            for cat, keywords in category_keywords.items():
                if any(keyword in text.lower() for keyword in keywords):
                    category = cat
                    break
            
            description = " ".join([w for w in words if not w.isdigit()])
            
            if amount:
                space_id = ensure_user_has_personal_space(user.id, user.first_name)
                add_expense(user.id, user.first_name, amount, category, description, space_id)
                
                response = f"""✅ **Голосовая трата добавлена!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {amount} руб
📂 **Категория:** {category}"""
                
                if description:
                    response += f"\n📝 **Комментарий:** {description}"
                
                await update.message.reply_text(response, reply_markup=get_main_keyboard())
            else:
                await update.message.reply_text(
                    f"❌ Не удалось распознать сумму в сообщении: *{text}*\n\n"
                    "💡 **Попробуйте сказать четче:**\n"
                    "• '500 продукты'\n" 
                    "• '1000 такси до работы'",
                    parse_mode='Markdown',
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

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фото чеков с улучшенным распознаванием"""
    try:
        user = update.effective_user
        
        if not TESSERACT_AVAILABLE:
            await update.message.reply_text(
                "❌ Распознавание чеков временно недоступно.\n\n"
                "💡 **Вы можете:**\n"
                "• Ввести трату вручную через текстовый ввод\n"
                "• Отправить голосовое сообщение\n"
                "• Написать текстом: '500 продукты'",
                reply_markup=get_main_keyboard()
            )
            return
        
        photo = update.message.photo[-1]
        processing_msg = await update.message.reply_text("📸 Обрабатываю фото чека...")
        
        try:
            # Скачиваем фото
            photo_file = await photo.get_file()
            
            # Создаем временный файл
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                temp_path = temp_file.name
            
            # Скачиваем файл
            await photo_file.download_to_drive(temp_path)
            
            # Читаем байты для обработки
            with open(temp_path, 'rb') as f:
                photo_bytes = f.read()
            
            logger.info(f"📷 Получено фото: {len(photo_bytes)} байт")
            
            # Обрабатываем чек с улучшенным распознаванием
            receipt_data = await process_receipt_photo(photo_bytes)
            
            if receipt_data and receipt_data['total'] > 0:
                # Улучшенное определение категории
                store_name = receipt_data.get('store', '')
                category = "Другое"
                
                if any(word in store_name.lower() for word in ['магазин', 'супермаркет', 'продукт']):
                    category = "Продукты"
                elif any(word in store_name.lower() for word in ['кафе', 'ресторан', 'кофе', 'столов']):
                    category = "Кафе"
                elif any(word in store_name.lower() for word in ['аптек', 'лекарств', 'медицин']):
                    category = "Здоровье"
                elif any(word in store_name.lower() for word in ['заправк', 'бензин', 'авто']):
                    category = "Транспорт"
                
                description = f"Чек {store_name}".strip() if store_name else "Распознанный чек"
                
                response = f"""📸 **Чек распознан!**

💸 **Сумма:** {receipt_data['total']} руб
📂 **Категория:** {category}"""
                
                if store_name:
                    response += f"\n🏪 **Магазин:** {store_name}"

                await processing_msg.delete()
                
                # Спрашиваем подтверждение
                confirm_keyboard = [
                    ["✅ Да, сохранить", "❌ Отменить"]
                ]
                reply_markup = ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True)
                
                await update.message.reply_text(
                    response + "\n\nСохраняем трату?",
                    reply_markup=reply_markup
                )
                
                # Сохраняем данные для подтверждения
                context.user_data['pending_receipt'] = {
                    'amount': receipt_data['total'],
                    'category': category,
                    'description': description,
                    'store': store_name
                }
                
            else:
                await processing_msg.edit_text(
                    "❌ Не удалось распознать чек.\n\n"
                    "💡 **Попробуйте:**\n"
                    "• Сфотографировать чек более четко\n"
                    "• Убедиться, что фото хорошо освещено\n"
                    "• Сфотографировать только область с суммой\n"
                    "• Или ввести данные вручную через форму",
                    reply_markup=get_main_keyboard()
                )
                
        finally:
            # Удаляем временный файл
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.unlink(temp_path)
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото: {str(e)}")
        try:
            await processing_msg.delete()
        except:
            pass
        await update.message.reply_text(
            "❌ Ошибка при обработке фото. Попробуйте текстовый ввод.",
            reply_markup=get_main_keyboard()
        )

# ===== ЗАПУСК ПРИЛОЖЕНИЯ =====
def run_bot():
    """Запуск Telegram бота в отдельном потоке"""
    try:
        # Создаем приложение бота
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN не найден в переменных окружения")
            return
        
        global application
        application = Application.builder().token(bot_token).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
        
        logger.info("🤖 Бот запускается...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")

def run_flask():
    """Запуск Flask приложения"""
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False)

def main():
    """Основная функция запуска"""
    # Инициализация базы данных
    init_db()
    logger.info("✅ База данных инициализирована")
    
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("✅ Бот запущен в отдельном потоке")
    
    # Запускаем Flask (основной поток для Railway)
    logger.info("🚀 Запускаем Flask приложение...")
    run_flask()

if __name__ == "__main__":
    main()
