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

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app для API
flask_app = Flask(__name__)

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
        logger.info(f"✅ Пространство создано успешно: ID={space_id}")
        return space_id, invite_code
    except Exception as e:
        logger.error(f"❌ Ошибка создания финансового пространства: {e}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return None, None
    finally:
        conn.close()

def add_expense(user_id, user_name, amount, category, description, space_id):
    """Добавление траты"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('''INSERT INTO expenses (user_id, user_name, space_id, amount, category, description)
                         VALUES (?, ?, ?, ?, ?, ?)''', 
                     (user_id, user_name, space_id, amount, category, description))
        else:
            c = conn.cursor()
            c.execute('''INSERT INTO expenses (user_id, user_name, space_id, amount, category, description)
                         VALUES (%s, %s, %s, %s, %s, %s)''', 
                     (user_id, user_name, space_id, amount, category, description))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка добавления траты: {e}")
        return False
    finally:
        conn.close()

def set_user_budget(user_id, space_id, amount):
    """Установка бюджета пользователя"""
    conn = get_db_connection()
    
    try:
        month_year = datetime.now().strftime('%Y-%m')
        
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('''INSERT OR REPLACE INTO budgets (user_id, space_id, amount, month_year)
                         VALUES (?, ?, ?, ?)''', 
                     (user_id, space_id, amount, month_year))
        else:
            c = conn.cursor()
            c.execute('''INSERT INTO budgets (user_id, space_id, amount, month_year)
                         VALUES (%s, %s, %s, %s)
                         ON CONFLICT (user_id, space_id, month_year) 
                         DO UPDATE SET amount = EXCLUDED.amount''', 
                     (user_id, space_id, amount, month_year))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка установки бюджета: {e}")
        return False
    finally:
        conn.close()

def get_user_budget(user_id, space_id):
    """Получение бюджета пользователя"""
    conn = get_db_connection()
    
    try:
        month_year = datetime.now().strftime('%Y-%m')
        
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT amount FROM budgets 
                       WHERE user_id = ? AND space_id = ? AND month_year = ?'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id, month_year))
        else:
            query = '''SELECT amount FROM budgets 
                       WHERE user_id = %s AND space_id = %s AND month_year = %s'''
            df = pd.read_sql_query(query, conn, params=(user_id, space_id, month_year))
        
        if not df.empty:
            return float(df.iloc[0]['amount'])
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка получения бюджета: {e}")
        return None
    finally:
        conn.close()

def remove_member_from_space(space_id, member_id, current_user_id):
    """Удаление участника из пространства"""
    conn = get_db_connection()
    
    try:
        # Проверяем, что текущий пользователь имеет права
        if not is_user_admin_in_space(current_user_id, space_id):
            return False, "Недостаточно прав"
        
        # Нельзя удалить владельца
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('''SELECT role FROM space_members WHERE space_id = ? AND user_id = ?''', (space_id, member_id))
            result = c.fetchone()
            
            if result and result[0] == 'owner':
                return False, "Нельзя удалить владельца пространства"
            
            c.execute('''DELETE FROM space_members WHERE space_id = ? AND user_id = ?''', (space_id, member_id))
        else:
            c = conn.cursor()
            c.execute('''SELECT role FROM space_members WHERE space_id = %s AND user_id = %s''', (space_id, member_id))
            result = c.fetchone()
            
            if result and result[0] == 'owner':
                return False, "Нельзя удалить владельца пространства"
            
            c.execute('''DELETE FROM space_members WHERE space_id = %s AND user_id = %s''', (space_id, member_id))
        
        conn.commit()
        return True, "Участник удален"
    except Exception as e:
        logger.error(f"❌ Ошибка удаления участника: {e}")
        return False, str(e)
    finally:
        conn.close()

# ===== TELEGRAM BOT HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"👋 Пользователь {user.first_name} (ID: {user.id}) запустил бота")
    
    # Проверяем и создаем личное пространство если нужно
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT 1 FROM space_members WHERE user_id = ?'''
            df = pd.read_sql_query(query, conn, params=(user.id,))
        else:
            query = '''SELECT 1 FROM space_members WHERE user_id = %s'''
            df = pd.read_sql_query(query, conn, params=(user.id,))
        
        if df.empty:
            create_personal_space(user.id, user.first_name)
            logger.info(f"✅ Создано личное пространство для пользователя {user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка проверки личного пространства: {e}")
    finally:
        conn.close()
    
    # Создаем клавиатуру с WebApp кнопкой
    keyboard = [
        [KeyboardButton("📱 Открыть приложение", web_app=WebAppInfo(url=f"https://{os.environ.get('RAILWAY_STATIC_URL', 'your-domain.railway.app')}"))],
        [KeyboardButton("➕ Добавить трату"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("👥 Мои пространства"), KeyboardButton("🔗 Присоединиться")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу тебе управлять финансами:\n"
        "• 📱 Открыть веб-приложение для полного функционала\n"
        "• ➕ Добавлять траты\n" 
        "• 📊 Смотреть статистику\n"
        "• 👥 Создавать совместные пространства\n\n"
        "Нажми кнопку ниже, чтобы открыть приложение! 📱"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    text = update.message.text
    user = update.effective_user
    
    if text == "➕ Добавить трату":
        await update.message.reply_text("Используйте веб-приложение для удобного добавления трат! 📱\n\nНажмите кнопку '📱 Открыть приложение'")
    
    elif text == "📊 Статистика":
        await update.message.reply_text("Вся статистика доступна в веб-приложении! 📊\n\nНажмите кнопку '📱 Открыть приложение'")
    
    elif text == "👥 Мои пространства":
        await update.message.reply_text("Управление пространствами в веб-приложении! 👥\n\nНажмите кнопку '📱 Открыть приложение'")
    
    elif text == "🔗 Присоединиться":
        await update.message.reply_text("Для присоединения к пространству используйте веб-приложение или получите пригласительный код от администратора! 🔗")
    
    else:
        await update.message.reply_text("Используйте кнопки ниже или откройте веб-приложение для полного функционала! 📱")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фото (для распознавания чеков)"""
    user = update.effective_user
    
    if not TESSERACT_AVAILABLE:
        await update.message.reply_text(
            "❌ Распознавание чеков временно недоступно. "
            "Пожалуйста, введите трату вручную через веб-приложение."
        )
        return
    
    await update.message.reply_text("🔍 Обрабатываю фото чека...")
    
    try:
        # Получаем фото
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        
        # Обрабатываем чек
        receipt_data = await process_receipt_photo(photo_bytes)
        
        if receipt_data and receipt_data['total'] > 0:
            message = (
                f"✅ Чек распознан!\n\n"
                f"🏪 Магазин: {receipt_data['store'] or 'Не определено'}\n"
                f"💰 Сумма: {receipt_data['total']:.2f} руб.\n\n"
                f"Для добавления траты используйте веб-приложение 📱"
            )
            
            # Сохраняем данные чека в контексте
            context.user_data['last_receipt'] = receipt_data
            
        else:
            message = (
                "❌ Не удалось распознать сумму чека.\n\n"
                "Пожалуйста, введите трату вручную через веб-приложение."
            )
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке фото. "
            "Пожалуйста, попробуйте еще раз или введите трату вручную."
        )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений"""
    user = update.effective_user
    await update.message.reply_text("🎤 Голосовые команды скоро будут доступны! Пока используйте текстовый ввод или веб-приложение.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"❌ Ошибка: {context.error}", exc_info=context.error)

# ===== MAIN APPLICATION SETUP =====
def main():
    """Основная функция запуска приложения"""
    # Инициализация базы данных
    init_db()
    logger.info("✅ База данных инициализирована")
    
    # Создаем приложение бота
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("❌ TELEGRAM_BOT_TOKEN не найден в переменных окружения")
        return
    
    application = Application.builder().token(bot_token).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_error_handler(error_handler)
    
    logger.info("🤖 Бот запущен и готов к работе")
    
    # Запускаем бота (не в отдельном потоке!)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# Запуск Flask приложения для Railway
if __name__ == "__main__":
    # В Railway приложение запускается через gunicorn, поэтому просто инициализируем базу
    init_db()
    logger.info("🚀 Приложение запущено на Railway")
