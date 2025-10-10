import sqlite3
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import os
import json
import tempfile
import re
import io
import subprocess
import sys
from PIL import Image
import speech_recognition as sr
import numpy as np
import psycopg2
from urllib.parse import urlparse
import logging
from flask import Flask, request, jsonify
import hashlib
import hmac
import time

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
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

# ===== ВАЛИДАЦИЯ WEBAPP DATA =====
def validate_webapp_data(init_data):
    """Валидация данных от Telegram WebApp"""
    try:
        if not init_data:
            return False
            
        # Проверяем подпись (упрощенная версия)
        # В реальном приложении нужно проверять хэш
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка валидации WebApp данных: {e}")
        return False

def get_user_from_init_data(init_data):
    """Извлечение данных пользователя из initData"""
    try:
        # Парсим initData строку
        params = {}
        for item in init_data.split('&'):
            key, value = item.split('=')
            params[key] = value
        
        # Декодируем user данные
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
                'id': row['id'],
                'name': row['name'],
                'description': row['description'],
                'space_type': row['space_type'],
                'invite_code': row['invite_code'],
                'member_count': row['member_count']
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
            query = '''SELECT user_name, role, joined_at 
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
            query = '''SELECT user_name, role, joined_at 
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
        
        if not validate_webapp_data(init_data):
            return jsonify({'error': 'Invalid data'}), 401
            
        user_data = get_user_from_init_data(init_data)
        if not user_data:
            return jsonify({'error': 'User not found'}), 401
            
        if not name or not space_type:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Создаем пространство
        space_id, invite_code = create_financial_space(
            name, description, space_type, 
            user_data['id'], user_data['first_name']
        )
        
        if space_id:
            return jsonify({
                'success': True,
                'space_id': space_id,
                'invite_code': invite_code
            })
        else:
            return jsonify({'error': 'Failed to create space'}), 500
            
    except Exception as e:
        logger.error(f"❌ API Error in create_space: {e}")
        return jsonify({'error': 'Internal server error'}), 500

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
            amount, category, description, space_id
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
            # Статистика по категориям
            query = '''SELECT category, SUM(amount) as total, COUNT(*) as count
                       FROM expenses 
                       WHERE space_id = ?
                       GROUP BY category 
                       ORDER BY total DESC'''
            df = pd.read_sql_query(query, conn, params=(space_id,))
            
            # Общее количество трат
            count_query = '''SELECT COUNT(*) as total_count FROM expenses WHERE space_id = ?'''
            count_df = pd.read_sql_query(count_query, conn, params=(space_id,))
            
        else:
            query = '''SELECT category, SUM(amount) as total, COUNT(*) as count
                       FROM expenses 
                       WHERE space_id = %s
                       GROUP BY category 
                       ORDER BY total DESC'''
            df = pd.read_sql_query(query, conn, params=(space_id,))
            
            count_query = '''SELECT COUNT(*) as total_count FROM expenses WHERE space_id = %s'''
            count_df = pd.read_sql_query(count_query, conn, params=(space_id,))
        
        conn.close()
        
        categories = []
        for _, row in df.iterrows():
            categories.append({
                'category': row['category'],
                'total': float(row['total']),
                'count': row['count']
            })
        
        return jsonify({
            'categories': categories,
            'total_count': count_df.iloc[0]['total_count'] if not count_df.empty else 0
        })
            
    except Exception as e:
        logger.error(f"❌ API Error in get_analytics: {e}")
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
    
    if space_type == 'personal':
        return create_personal_space(created_by, created_by_name)
    
    import random
    import string
    invite_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    
    try:
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
        return space_id, invite_code
    except Exception as e:
        logger.error(f"❌ Ошибка создания пространства: {e}")
        return None, None
    finally:
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

# ===== ОБРАБОТЧИКИ ГОЛОСА И ФОТО (сохраняем существующий функционал) =====
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
    """Обработчик фото чеков"""
    try:
        user = update.effective_user
        
        # Проверяем доступность Tesseract
        try:
            import pytesseract
            TESSERACT_AVAILABLE = True
        except:
            TESSERACT_AVAILABLE = False
        
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
            
            # Обрабатываем через Tesseract
            image = Image.open(temp_path)
            text = pytesseract.image_to_string(image, lang='rus+eng')
            
            if not text.strip():
                await processing_msg.edit_text(
                    "❌ Не удалось распознать чек.\n\n"
                    "💡 **Попробуйте:**\n"
                    "• Сфотографировать чек более четко\n"
                    "• Убедиться, что фото хорошо освещено\n"
                    "• Или ввести данные вручную"
                )
                return
            
            # Простой парсинг чека
            lines = text.split('\n')
            total_amount = 0
            
            for line in lines:
                # Ищем суммы
                amounts = re.findall(r'(\d+[.,]\d+)', line)
                for amount_str in amounts:
                    try:
                        amount = float(amount_str.replace(',', '.'))
                        if 10 <= amount <= 100000 and amount > total_amount:
                            total_amount = amount
                    except:
                        pass
            
            if total_amount > 0:
                category = "Другое"
                description = "Распознанный чек"
                
                space_id = ensure_user_has_personal_space(user.id, user.first_name)
                add_expense(user.id, user.first_name, total_amount, category, description, space_id)
                
                response = f"""✅ **Трата из чека добавлена!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {total_amount} руб
📂 **Категория:** {category}
📝 **Комментарий:** {description}"""
                
                await processing_msg.delete()
                await update.message.reply_text(response, reply_markup=get_main_keyboard())
            else:
                await processing_msg.edit_text(
                    "❌ Не удалось распознать сумму в чеке.\n\n"
                    "💡 Попробуйте сфотографировать более четко область с итоговой суммой."
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

# ===== ЗАПУСК ПРИЛОЖЕНИЯ =====
def run_flask():
    """Запуск Flask приложения для API"""
    port = int(os.environ.get('FLASK_PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False)

def main():
    # Инициализация базы данных
    init_db()
    
    # Получение токена
    BOT_TOKEN = os.environ.get('BOT_TOKEN', '7911885739:AAGrMekWmLgz_ej8JDFqG-CbDA5Nie7vKFc')
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        return
    
    # Запуск Flask в отдельном потоке
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask API запущен")
    
    # Создание приложения бота
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    
    # Запуск бота
    logger.info("🚀 Бот запускается...")
    application.run_polling()

if __name__ == "__main__":
    main()

