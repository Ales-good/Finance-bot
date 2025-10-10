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
    """Инициализация базы данных с поддержкой многоуровневых пространств"""
    conn = get_db_connection()
    
    if isinstance(conn, sqlite3.Connection):
        # SQLite
        c = conn.cursor()
        
        # Таблица финансовых пространств
        c.execute('''CREATE TABLE IF NOT EXISTS financial_spaces
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT NOT NULL,
                      description TEXT,
                      space_type TEXT DEFAULT 'personal', -- personal, private, public
                      created_by INTEGER,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      invite_code TEXT UNIQUE,
                      is_active BOOLEAN DEFAULT TRUE,
                      privacy_settings TEXT DEFAULT '{"view_all": true}')''')
        
        # Таблица участников пространств
        c.execute('''CREATE TABLE IF NOT EXISTS space_members
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      space_id INTEGER,
                      user_id INTEGER,
                      user_name TEXT,
                      role TEXT DEFAULT 'member', -- owner, admin, member
                      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (space_id) REFERENCES financial_spaces (id))''')
        
        # Обновляем таблицу expenses - добавляем space_id и visibility
        c.execute('''CREATE TABLE IF NOT EXISTS expenses_new
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER, 
                      user_name TEXT,
                      space_id INTEGER,
                      amount REAL, 
                      category TEXT, 
                      description TEXT, 
                      date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      visibility TEXT DEFAULT 'full', -- full, anonymous, stats_only
                      FOREIGN KEY (space_id) REFERENCES financial_spaces (id))''')
        
        # Переносим данные из старой таблицы (для существующих пользователей)
        try:
            c.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="expenses"')
            if c.fetchone():
                c.execute('''INSERT INTO expenses_new (id, user_id, user_name, amount, category, description, date)
                             SELECT id, user_id, user_name, amount, category, description, date FROM expenses''')
                c.execute('DROP TABLE expenses')
        except:
            pass
        
        c.execute('ALTER TABLE expenses_new RENAME TO expenses')
        
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
                      is_active BOOLEAN DEFAULT TRUE,
                      privacy_settings TEXT DEFAULT '{"view_all": true}')''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS space_members
                     (id SERIAL PRIMARY KEY,
                      space_id INTEGER REFERENCES financial_spaces(id),
                      user_id BIGINT,
                      user_name TEXT,
                      role TEXT DEFAULT 'member',
                      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Добавляем недостающие колонки к существующей таблице expenses
        try:
            c.execute('ALTER TABLE expenses ADD COLUMN space_id INTEGER REFERENCES financial_spaces(id)')
        except:
            pass
        
        try:
            c.execute('ALTER TABLE expenses ADD COLUMN visibility TEXT DEFAULT \'full\'')
        except:
            pass
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных с многоуровневой системой пространств инициализирована")

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

# ===== ГОЛОСОВОЕ РАСПОЗНАВАНИЕ =====
class VoiceRecognizer:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.pause_threshold = 0.8
        self.recognizer.dynamic_energy_threshold = True
        
        # Проверяем доступность Google Speech Recognition
        self.google_available = True  # По умолчанию доступен
        logger.info("✅ Голосовое распознавание инициализировано")
    
    async def transcribe_audio(self, audio_path):
        """Транскрибируем аудио файл"""
        logger.info(f"🎤 Распознаю аудио: {audio_path}")
        
        try:
            with sr.AudioFile(audio_path) as source:
                # Убираем шумы и настраиваем чувствительность
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.record(source)
                
                # Пробуем распознать через Google
                try:
                    text = self.recognizer.recognize_google(audio, language='ru-RU')
                    logger.info(f"✅ Распознано: {text}")
                    return text
                except sr.UnknownValueError:
                    logger.warning("❌ Не удалось распознать речь")
                    return None
                except sr.RequestError as e:
                    logger.warning(f"❌ Ошибка Google Speech Recognition: {e}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Ошибка обработки аудио: {e}")
            return None

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
            'Продукты': ['продукт', 'еда', 'магазин', 'супермаркет', 'покупк', 'молоко', 'хлеб', 'мясо', 'рыба', 'овощ', 'фрукт'],
            'Кафе': ['кафе', 'ресторан', 'кофе', 'обед', 'ужин', 'завтрак', 'столов', 'бургер', 'пицц', 'суши'],
            'Транспорт': ['транспорт', 'такси', 'метро', 'автобус', 'бензин', 'заправк', 'парковк', 'такса', 'uber', 'яндекс.такси'],
            'Дом': ['дом', 'квартир', 'коммунал', 'аренд', 'ремонт', 'ипотек', 'мебель', 'бытов', 'техник'],
            'Одежда': ['одежд', 'обув', 'шопинг', 'вещ', 'магазин', 'бренд', 'куртк', 'джинс', 'футболк'],
            'Здоровье': ['здоров', 'аптек', 'врач', 'лекарств', 'больнич', 'стоматолог', 'поликлиник', 'анализ'],
            'Развлечения': ['развлечен', 'кино', 'концерт', 'театр', 'клуб', 'бар', 'дискотек', 'караоке', 'билет'],
            'Подписки': ['подписк', 'интернет', 'телефон', 'связ', 'мобильн', 'ютуб', 'нетфликс', 'спотифай', 'яндекс.плюс'],
            'Маркетплейсы': ['wildberries', 'озон', 'яндекс маркет', 'алиэкспресс', 'маркетплейс', 'интернет-магазин'],
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
        r'(?:чек|сумма)[^\d]*(\d+[.,]\d+)',
        r'(?:цена|стоимость)[^\d]*(\d+[.,]\d+)',
    ]
    
    # Поиск магазина
    store_patterns = [
        r'([А-ЯЁ][а-яё]+\s*[А-ЯЁ]?[а-яё]*\s*(?:магазин|супермаркет|торговый|центр))',
        r'([А-ЯЁ][а-яё]+\s*[А-ЯЁ]?[а-яё]*)',
    ]
    
    # Поиск по паттернам
    for line in lines:
        line_clean = re.sub(r'[^\w\s\d.,]', '', line.lower())
        
        # Поиск суммы
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
        
        # Поиск магазина
        if not receipt_data['store']:
            for pattern in store_patterns:
                matches = re.findall(pattern, line)
                if matches:
                    receipt_data['store'] = matches[0]
                    logger.info(f"🏪 Найден магазин: {receipt_data['store']}")
                    break
    
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
        
        # Увеличиваем контрастность
        image = image.convert('L')  # В grayscale
        # Применяем фильтр для улучшения читаемости
        from PIL import ImageEnhance
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)  # Увеличиваем контрастность
        
        # Распознаем текст
        text = pytesseract.image_to_string(image, lang='rus+eng')
        
        if not text.strip():
            logger.warning("❌ Не удалось распознать текст")
            return None
        
        logger.info(f"✅ Распознано символов: {len(text)}")
        logger.info(f"📄 Текст чека: {text[:200]}...")
        return parse_receipt_text(text)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки чека: {e}")
        return None

# ===== СИСТЕМА ПРОСТРАНСТВ =====
def create_personal_space(user_id, user_name):
    """Создание личного пространства для нового пользователя"""
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

def create_financial_space(name, description, space_type, created_by, created_by_name, privacy_settings=None):
    """Создание нового финансового пространства"""
    conn = get_db_connection()
    
    if space_type == 'personal':
        return create_personal_space(created_by, created_by_name)
    
    invite_code = generate_invite_code()
    
    if privacy_settings is None:
        if space_type == 'private':
            privacy_settings = '{"view_all": true}'
        else:  # public
            privacy_settings = '{"view_all": false, "show_stats_only": true}'
    
    try:
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('''INSERT INTO financial_spaces (name, description, space_type, created_by, invite_code, privacy_settings)
                         VALUES (?, ?, ?, ?, ?, ?)''', 
                     (name, description, space_type, created_by, invite_code, privacy_settings))
            space_id = c.lastrowid
            
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (?, ?, ?, ?)''', (space_id, created_by, created_by_name, 'owner'))
        else:
            c = conn.cursor()
            c.execute('''INSERT INTO financial_spaces (name, description, space_type, created_by, invite_code, privacy_settings)
                         VALUES (%s, %s, %s, %s, %s, %s) RETURNING id''', 
                     (name, description, space_type, created_by, invite_code, privacy_settings))
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

def generate_invite_code():
    """Генерация уникального кода приглашения"""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def join_financial_space(invite_code, user_id, user_name):
    """Присоединение пользователя к пространству"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            # Находим пространство по коду
            c.execute('SELECT id, space_type FROM financial_spaces WHERE invite_code = ? AND is_active = TRUE', (invite_code,))
            result = c.fetchone()
            
            if not result:
                return False, "Пространство не найдено или код неверен"
            
            space_id, space_type = result
            
            # Проверяем, не состоит ли уже пользователь
            c.execute('SELECT id FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, user_id))
            if c.fetchone():
                return False, "Вы уже состоите в этом пространстве"
            
            # Добавляем пользователя
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (?, ?, ?, ?)''', (space_id, user_id, user_name, 'member'))
        else:
            c = conn.cursor()
            c.execute('SELECT id, space_type FROM financial_spaces WHERE invite_code = %s AND is_active = TRUE', (invite_code,))
            result = c.fetchone()
            
            if not result:
                return False, "Пространство не найдено или код неверен"
            
            space_id, space_type = result
            
            c.execute('SELECT id FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, user_id))
            if c.fetchone():
                return False, "Вы уже состоите в этом пространстве"
            
            c.execute('''INSERT INTO space_members (space_id, user_id, user_name, role)
                         VALUES (%s, %s, %s, %s)''', (space_id, user_id, user_name, 'member'))
        
        conn.commit()
        
        space_type_name = {
            'personal': 'личное',
            'private': 'закрытое',
            'public': 'публичное'
        }.get(space_type, space_type)
        
        return True, f"Вы успешно присоединились к {space_type_name} пространству"
    except Exception as e:
        logger.error(f"❌ Ошибка присоединения к пространству: {e}")
        return False, "Ошибка при присоединении"
    finally:
        conn.close()

def get_user_spaces(user_id):
    """Получение всех пространств пользователя"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT fs.id, fs.name, fs.description, fs.space_type, fs.invite_code, sm.role
                       FROM financial_spaces fs
                       JOIN space_members sm ON fs.id = sm.space_id
                       WHERE sm.user_id = ? AND fs.is_active = TRUE
                       ORDER BY 
                         CASE fs.space_type 
                           WHEN 'personal' THEN 1 
                           WHEN 'private' THEN 2 
                           ELSE 3 
                         END, fs.created_at DESC'''
            df = pd.read_sql_query(query, conn, params=(user_id,))
        else:
            query = '''SELECT fs.id, fs.name, fs.description, fs.space_type, fs.invite_code, sm.role
                       FROM financial_spaces fs
                       JOIN space_members sm ON fs.id = sm.space_id
                       WHERE sm.user_id = %s AND fs.is_active = TRUE
                       ORDER BY 
                         CASE fs.space_type 
                           WHEN 'personal' THEN 1 
                           WHEN 'private' THEN 2 
                           ELSE 3 
                         END, fs.created_at DESC'''
            df = pd.read_sql_query(query, conn, params=(user_id,))
        
        return df
    except Exception as e:
        logger.error(f"❌ Ошибка получения пространств: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_space_members(space_id):
    """Получение участников пространства"""
    conn = get_db_connection()
    
    try:
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
        
        return df
    except Exception as e:
        logger.error(f"❌ Ошибка получения участников: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_space_privacy_settings(space_id):
    """Получение настроек приватности пространства"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('SELECT privacy_settings FROM financial_spaces WHERE id = ?', (space_id,))
            result = c.fetchone()
        else:
            c = conn.cursor()
            c.execute('SELECT privacy_settings FROM financial_spaces WHERE id = %s', (space_id,))
            result = c.fetchone()
        
        if result and result[0]:
            return json.loads(result[0])
        return {"view_all": True, "show_stats_only": False}
    except Exception as e:
        logger.error(f"❌ Ошибка получения настроек приватности: {e}")
        return {"view_all": True, "show_stats_only": False}
    finally:
        conn.close()

def ensure_user_has_personal_space(user_id, user_name):
    """Гарантирует, что у пользователя есть личное пространство"""
    spaces = get_user_spaces(user_id)
    personal_spaces = spaces[spaces['space_type'] == 'personal']
    
    if personal_spaces.empty:
        return create_personal_space(user_id, user_name)
    else:
        return personal_spaces.iloc[0]['id']

# ===== ФУНКЦИИ УПРАВЛЕНИЯ УЧАСТНИКАМИ =====
def remove_member_from_space(space_id, user_id, remover_id):
    """Удаление участника из пространства"""
    conn = get_db_connection()
    
    try:
        # Проверяем права удаляющего
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('SELECT role FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, remover_id))
            remover_role = c.fetchone()
            
            if not remover_role or remover_role[0] not in ['owner', 'admin']:
                return False, "Недостаточно прав для удаления участников"
            
            # Не позволяем владельцу удалить себя
            c.execute('SELECT role FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, user_id))
            target_role = c.fetchone()
            
            if target_role and target_role[0] == 'owner':
                return False, "Нельзя удалить владельца пространства"
            
            # Удаляем участника
            c.execute('DELETE FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, user_id))
        else:
            c = conn.cursor()
            c.execute('SELECT role FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, remover_id))
            remover_role = c.fetchone()
            
            if not remover_role or remover_role[0] not in ['owner', 'admin']:
                return False, "Недостаточно прав для удаления участников"
            
            c.execute('SELECT role FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, user_id))
            target_role = c.fetchone()
            
            if target_role and target_role[0] == 'owner':
                return False, "Нельзя удалить владельца пространства"
            
            c.execute('DELETE FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, user_id))
        
        conn.commit()
        return True, "Участник успешно удален"
    except Exception as e:
        logger.error(f"❌ Ошибка удаления участника: {e}")
        return False, "Ошибка при удалении участника"
    finally:
        conn.close()

def change_member_role(space_id, user_id, new_role, changer_id):
    """Изменение роли участника"""
    conn = get_db_connection()
    
    try:
        # Проверяем права изменяющего
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            c.execute('SELECT role FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, changer_id))
            changer_role = c.fetchone()
            
            if not changer_role or changer_role[0] != 'owner':
                return False, "Только владелец может изменять роли"
            
            # Не позволяем изменить роль владельца
            if user_id == changer_id and new_role != 'owner':
                return False, "Нельзя изменить свою роль владельца"
            
            c.execute('UPDATE space_members SET role = ? WHERE space_id = ? AND user_id = ?', 
                     (new_role, space_id, user_id))
        else:
            c = conn.cursor()
            c.execute('SELECT role FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, changer_id))
            changer_role = c.fetchone()
            
            if not changer_role or changer_role[0] != 'owner':
                return False, "Только владелец может изменять роли"
            
            if user_id == changer_id and new_role != 'owner':
                return False, "Нельзя изменить свою роль владельца"
            
            c.execute('UPDATE space_members SET role = %s WHERE space_id = %s AND user_id = %s', 
                     (new_role, space_id, user_id))
        
        conn.commit()
        return True, f"Роль изменена на {new_role}"
    except Exception as e:
        logger.error(f"❌ Ошибка изменения роли: {e}")
        return False, "Ошибка при изменении роли"
    finally:
        conn.close()

def leave_space(space_id, user_id):
    """Выход пользователя из пространства"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            c = conn.cursor()
            
            # Проверяем, не является ли пользователь владельцем
            c.execute('SELECT role FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, user_id))
            user_role = c.fetchone()
            
            if user_role and user_role[0] == 'owner':
                # Если владелец - проверяем, есть ли другие участники
                c.execute('SELECT COUNT(*) FROM space_members WHERE space_id = ? AND user_id != ?', (space_id, user_id))
                other_members = c.fetchone()[0]
                
                if other_members > 0:
                    return False, "Владелец не может покинуть пространство с другими участниками. Сначала передайте владение или удалите других участников."
                else:
                    # Если участников нет - удаляем пространство
                    c.execute('DELETE FROM financial_spaces WHERE id = ?', (space_id,))
            
            c.execute('DELETE FROM space_members WHERE space_id = ? AND user_id = ?', (space_id, user_id))
        else:
            c = conn.cursor()
            
            c.execute('SELECT role FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, user_id))
            user_role = c.fetchone()
            
            if user_role and user_role[0] == 'owner':
                c.execute('SELECT COUNT(*) FROM space_members WHERE space_id = %s AND user_id != %s', (space_id, user_id))
                other_members = c.fetchone()[0]
                
                if other_members > 0:
                    return False, "Владелец не может покинуть пространство с другими участниками"
                else:
                    c.execute('DELETE FROM financial_spaces WHERE id = %s', (space_id,))
            
            c.execute('DELETE FROM space_members WHERE space_id = %s AND user_id = %s', (space_id, user_id))
        
        conn.commit()
        return True, "Вы вышли из пространства"
    except Exception as e:
        logger.error(f"❌ Ошибка выхода из пространства: {e}")
        return False, "Ошибка при выходе из пространства"
    finally:
        conn.close()

def get_detailed_space_members(space_id):
    """Получение подробной информации об участниках пространства"""
    conn = get_db_connection()
    
    try:
        if isinstance(conn, sqlite3.Connection):
            query = '''SELECT sm.user_id, sm.user_name, sm.role, sm.joined_at,
                              (SELECT COUNT(*) FROM expenses WHERE user_id = sm.user_id AND space_id = sm.space_id) as expense_count,
                              (SELECT SUM(amount) FROM expenses WHERE user_id = sm.user_id AND space_id = sm.space_id) as total_spent
                       FROM space_members sm
                       WHERE sm.space_id = ?
                       ORDER BY 
                         CASE sm.role 
                           WHEN 'owner' THEN 1 
                           WHEN 'admin' THEN 2 
                           ELSE 3 
                         END, sm.joined_at'''
            df = pd.read_sql_query(query, conn, params=(space_id,))
        else:
            query = '''SELECT sm.user_id, sm.user_name, sm.role, sm.joined_at,
                              (SELECT COUNT(*) FROM expenses WHERE user_id = sm.user_id AND space_id = sm.space_id) as expense_count,
                              (SELECT SUM(amount) FROM expenses WHERE user_id = sm.user_id AND space_id = sm.space_id) as total_spent
                       FROM space_members sm
                       WHERE sm.space_id = %s
                       ORDER BY 
                         CASE sm.role 
                           WHEN 'owner' THEN 1 
                           WHEN 'admin' THEN 2 
                           ELSE 3 
                         END, sm.joined_at'''
            df = pd.read_sql_query(query, conn, params=(space_id,))
        
        return df
    except Exception as e:
        logger.error(f"❌ Ошибка получения участников: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

# ===== ОБНОВЛЕННАЯ ФУНКЦИЯ ДОБАВЛЕНИЯ ТРАТ =====
def add_expense(user_id, user_name, amount, category, description="", space_id=None, visibility="full"):
    """Добавление траты в базу с поддержкой пространств"""
    try:
        # Если space_id не указан, используем личное пространство пользователя
        if space_id is None:
            space_id = ensure_user_has_personal_space(user_id, user_name)
        
        logger.info(f"💾 Сохраняем в базу: {user_name} - {amount} руб - {category} - space: {space_id}")
        
        conn = get_db_connection()
        c = conn.cursor()
        
        if isinstance(conn, sqlite3.Connection):
            c.execute('''INSERT INTO expenses (user_id, user_name, amount, category, description, space_id, visibility)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (user_id, user_name, amount, category, description, space_id, visibility))
        else:
            c.execute('''INSERT INTO expenses (user_id, user_name, amount, category, description, space_id, visibility)
                         VALUES (%s, %s, %s, %s, %s, %s, %s)''',
                      (user_id, user_name, amount, category, description, space_id, visibility))
        
        conn.commit()
        conn.close()
        logger.info(f"✅ Добавлена трата: {user_name} - {amount} руб - {category} - space: {space_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении в базу: {str(e)}")

# ===== КЛАВИАТУРЫ =====
def get_main_keyboard(user_id=None):
    """Основная клавиатура с учетом пространств"""
    web_app_url = os.environ.get('WEB_APP_URL', 'https://ales-good.github.io/Finance-bot/')
    
    if user_id:
        spaces = get_user_spaces(user_id)
        has_multiple_spaces = len(spaces) > 1
    else:
        has_multiple_spaces = False
    
    if has_multiple_spaces:
        keyboard = [
            [KeyboardButton("💸 Добавить трату", web_app=WebAppInfo(url=web_app_url))],
            [KeyboardButton("🏠 Мои пространства"), KeyboardButton("👥 Управление участниками")],
            [KeyboardButton("➕ Создать пространство"), KeyboardButton("🔗 Пригласить")],
            [KeyboardButton("📊 Статистика"), KeyboardButton("📅 Статистика за месяц")],
            [KeyboardButton("📝 Последние траты"), KeyboardButton("📈 Выгрузить в Excel")],
            [KeyboardButton("🆘 Помощь")]
        ]
    else:
        keyboard = [
            [KeyboardButton("💸 Добавить трату", web_app=WebAppInfo(url=web_app_url))],
            [KeyboardButton("➕ Создать пространство")],
            [KeyboardButton("📊 Статистика"), KeyboardButton("📅 Статистика за месяц")],
            [KeyboardButton("📝 Последние траты"), KeyboardButton("📈 Выгрузить в Excel")],
            [KeyboardButton("🆘 Помощь")]
        ]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_space_type_keyboard():
    """Клавиатура для выбора типа пространства"""
    keyboard = [
        [KeyboardButton("🏠 Личное пространство")],
        [KeyboardButton("👨‍👩‍👧‍👦 Закрытая группа (семья/друзья)")],
        [KeyboardButton("🌐 Публичное сообщество")],
        [KeyboardButton("❌ Отмена")]
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
    
    # Гарантируем, что у пользователя есть личное пространство
    ensure_user_has_personal_space(user.id, user.first_name)
    
    welcome_text = f"""
Привет, {user.first_name}! 👋

Я бот для учета финансов 💰 с поддержкой **многоуровневой системы пространств**!

🏠 **Типы пространств:**
• 🏠 **Личное** - только ваши траты (никто не видит)
• 👨‍👩‍👧‍👦 **Закрытые группы** - семья, друзья (общие траты)
• 🌐 **Публичные сообщества** - анонимная статистика

💸 **Основные возможности:**
• 📸 **Распознавание чеков** - отправьте фото чека
• 🎤 **Голосовой ввод** - говорите сумму и категорию
• 📊 **Подробная статистика** и аналитика
• 👥 **Совместные бюджеты** с друзьями и семьей
• 📈 **Выгрузка в Excel**

**🚀 Начните с создания своего первого пространства!**
"""

    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(user.id))

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
        space_id = context.user_data.get('current_space')
        
        add_expense(user.id, user.first_name, amount, category, description, space_id)
        
        response = f"""✅ **Трата добавлена через форму!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {amount} руб
📂 **Категория:** {category}"""
        
        if description:
            response += f"\n📝 **Комментарий:** {description}"
            
        await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
        
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
                    "• Или введите трату текстом"
                )
                return
            
            await processing_msg.edit_text(f"🎤 **Распознано:** _{text}_", parse_mode='Markdown')
            
            # Улучшенный парсинг текста
            words = text.lower().split()
            amount = None
            category = "Другое"
            description_words = []
            
            # Сначала ищем сумму (может быть в начале или содержать руб/рублей)
            for i, word in enumerate(words):
                # Пробуем извлечь сумму из чисел
                cleaned_word = re.sub(r'[^\d]', '', word)
                if cleaned_word:
                    try:
                        potential_amount = int(cleaned_word)
                        if 10 <= potential_amount <= 100000:  # Более реалистичный диапазон
                            amount = potential_amount
                            # Если есть слова "рубль", "руб", "р" - это точно сумма
                            if any(rub_word in word for rub_word in ['руб', 'р', '₽']):
                                break
                            # Если число в начале и достаточно большое - вероятно сумма
                            elif i == 0 and potential_amount >= 50:
                                break
                    except:
                        pass
            
            # Если сумму не нашли, пробуем найти в другом формате
            if not amount:
                # Ищем паттерны типа "пятьсот рублей"
                number_words = {
                    'сто': 100, 'двести': 200, 'триста': 300, 'четыреста': 400, 'пятьсот': 500,
                    'шестьсот': 600, 'семьсот': 700, 'восемьсот': 800, 'девятьсот': 900,
                    'тысяча': 1000, 'две тысячи': 2000, 'три тысячи': 3000, 'пять тысяч': 5000
                }
                for word, value in number_words.items():
                    if word in text.lower():
                        amount = value
                        break
            
            # Определяем категорию
            category, confidence = classifier.predict_category(text)
            
            # Формируем описание из оставшихся слов
            for word in words:
                if not word.isdigit() and word not in ['руб', 'рублей', 'р', '₽']:
                    # Пропускаем стоп-слова
                    if word not in ['на', 'за', 'в', 'для', 'купил', 'потратил', 'оплатил']:
                        description_words.append(word)
            
            description = ' '.join(description_words) if description_words else "Голосовая трата"
            
            if amount:
                preview_text = f"""🎤 **Голосовая трата распознана!**

💸 **Сумма:** {amount} руб
📂 **Категория:** {category}"""
                
                if description and description != "Голосовая трата":
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
                    f"❌ Не удалось распознать сумму в сообщении: *{text}*\n\n"
                    "💡 **Попробуйте сказать четче:**\n"
                    "• '500 продукты'\n" 
                    "• '1000 такси до работы'\n"
                    "• '250 кофе с круассаном'",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard(user.id)
                )
                
        finally:
            # Удаляем временный файл
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки голосового сообщения: {str(e)}")
        await update.message.reply_text(
            "❌ Ошибка при обработке голоса. Попробуйте текстовый ввод.",
            reply_markup=get_main_keyboard(user.id)
        )

# ===== ОБРАБОТЧИК ФОТО ЧЕКОВ =====
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        
        if not TESSERACT_AVAILABLE:
            await update.message.reply_text(
                "❌ Распознавание чеков временно недоступно.\n\n"
                "💡 **Вы можете:**\n"
                "• Ввести трату вручную через форму\n"
                "• Отправить голосовое сообщение\n"
                "• Написать текстом: '500 продукты'",
                reply_markup=get_main_keyboard(user.id)
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
            
            # Обрабатываем чек
            receipt_data = await process_receipt_photo(photo_bytes)
            
            if receipt_data and receipt_data['total'] > 0:
                category, confidence = classifier.predict_category(receipt_data.get('store', 'чек покупка'))
                store_name = receipt_data.get('store', '')
                description = f"Чек {store_name}".strip() if store_name else "Распознанный чек"
                
                preview_text = f"""📸 **Чек распознан!**

💸 **Сумма:** {receipt_data['total']} руб
📂 **Категория:** {category}"""
                
                if store_name:
                    preview_text += f"\n🏪 **Магазин:** {store_name}"

                await processing_msg.delete()
                await update.message.reply_text(
                    preview_text + "\n\nСохраняем трату?",
                    reply_markup=get_simple_confirmation_keyboard()
                )
                
                context.user_data['pending_receipt_expense'] = {
                    'amount': receipt_data['total'],
                    'category': category,
                    'description': description,
                    'store': store_name
                }
                
            else:
                await processing_msg.delete()
                await update.message.reply_text(
                    "❌ Не удалось распознать чек.\n\n"
                    "💡 **Попробуйте:**\n"
                    "• Сфотографировать чек более четко\n"
                    "• Убедиться, что фото хорошо освещено\n"
                    "• Или ввести данные вручную через форму",
                    reply_markup=get_main_keyboard(user.id)
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
            "❌ Ошибка при обработке фото. Попробуйте текстовый ввод или форму.",
            reply_markup=get_main_keyboard(user.id)
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
            
            if expense_data['description'] and expense_data['description'] != "Голосовая трата":
                response += f"\n📝 **Комментарий:** {expense_data['description']}"
                
            await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
            context.user_data.pop('pending_voice_expense', None)
            
        elif text == "❌ Отменить":
            await update.message.reply_text(
                "❌ Добавление голосовой траты отменено.",
                reply_markup=get_main_keyboard(user.id)
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
            
            if expense_data.get('store'):
                response += f"\n🏪 **Магазин:** {expense_data['store']}"
            elif expense_data['description']:
                response += f"\n📝 **Комментарий:** {expense_data['description']}"
                
            await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
            context.user_data.pop('pending_receipt_expense', None)
            
        elif text == "❌ Отменить":
            await update.message.reply_text(
                "❌ Добавление траты отменено.",
                reply_markup=get_main_keyboard(user.id)
            )
            context.user_data.pop('pending_receipt_expense', None)
        return

# ===== ОБРАБОТЧИКИ ПРОСТРАНСТВ =====
async def handle_create_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик создания пространства"""
    user = update.effective_user
    
    if context.args:
        # Создание через команду с аргументами (простое создание закрытой группы)
        space_name = ' '.join(context.args)
        space_id, invite_code = create_financial_space(
            space_name, 
            f"Группа создана {user.first_name}", 
            'private', 
            user.id, 
            user.first_name
        )
        
        if space_id:
            response = f"""👨‍👩‍👧‍👦 **Создана новая закрытая группа!**

📝 **Название:** {space_name}
👤 **Создатель:** {user.first_name}
🔑 **Код приглашения:** `{invite_code}`

💡 Поделитесь кодом с другими участниками!
👥 Используйте /space_members {space_id} для управления"""
        else:
            response = "❌ Ошибка при создании пространства"
            
        await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
    else:
        # Интерактивное создание с выбором типа
        response = """🏠 **Создание финансового пространства**

Выберите тип пространства:

🏠 **Личное** - только ваши траты
• Полная приватность
• Автоматически создается при старте

👨‍👩‍👧‍👦 **Закрытая группа** - семья/друзья  
• Общие траты участников
• Приглашение по коду
• Видимость всех трат

🌐 **Публичное сообщество** - тематическое
• Анонимная статистика
• Сравнение с другими
• Только агрегированные данные

Выберите тип пространства:"""
        
        await update.message.reply_text(response, reply_markup=get_space_type_keyboard())
        context.user_data['awaiting_space_creation'] = True

async def handle_space_type_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора типа пространства"""
    user = update.effective_user
    text = update.message.text
    
    if not context.user_data.get('awaiting_space_creation'):
        return
    
    space_type_map = {
        "🏠 Личное пространство": ("personal", "Личное пространство"),
        "👨‍👩‍👧‍👦 Закрытая группа (семья/друзья)": ("private", "Закрытая группа"),
        "🌐 Публичное сообщество": ("public", "Публичное сообщество")
    }
    
    if text in space_type_map:
        space_type, space_type_name = space_type_map[text]
        
        if space_type == "personal":
            # Личное пространство уже создано автоматически
            response = f"""🏠 **Личное пространство**

У вас уже есть личное пространство! Оно создается автоматически при первом использовании бота.

Все траты по умолчанию сохраняются в личное пространство."""
            
            await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
        
        else:
            context.user_data['selected_space_type'] = space_type
            context.user_data['selected_space_type_name'] = space_type_name
            
            response = f"""📝 **Создание {space_type_name.lower()}**

Введите название для нового пространства:"""
            
            await update.message.reply_text(response)
            context.user_data['awaiting_space_name'] = True
    
    elif text == "❌ Отмена":
        await update.message.reply_text("❌ Создание пространства отменено.", reply_markup=get_main_keyboard(user.id))
        context.user_data.pop('awaiting_space_creation', None)
    
    context.user_data.pop('awaiting_space_creation', None)

async def handle_space_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода названия пространства"""
    user = update.effective_user
    
    if not context.user_data.get('awaiting_space_name'):
        return
    
    space_name = update.message.text
    space_type = context.user_data.get('selected_space_type')
    space_type_name = context.user_data.get('selected_space_type_name')
    
    if space_type in ['private', 'public']:
        space_id, invite_code = create_financial_space(
            space_name,
            f"{space_type_name} создана {user.first_name}",
            space_type,
            user.id,
            user.first_name
        )
        
        if space_id:
            if space_type == 'private':
                response = f"""👨‍👩‍👧‍👦 **Создана новая закрытая группа!**

📝 **Название:** {space_name}
👤 **Создатель:** {user.first_name}
🔑 **Код приглашения:** `{invite_code}`

💡 **Особенности:**
• Все участники видят все траты
• Идеально для семьи и близких друзей
• Приватное пространство

👥 **Для управления участниками:**
`/space_members {space_id}`

💬 Поделитесь кодом с другими участниками!"""
            else:  # public
                response = f"""🌐 **Создано новое публичное сообщество!**

📝 **Название:** {space_name}
👤 **Создатель:** {user.first_name}
🔑 **Код приглашения:** `{invite_code}`

💡 **Особенности:**
• Участники видят только анонимную статистику
• Сравнение своих трат с сообществом
• Идеально для тематических групп

👥 Делитесь кодом для пополнения сообщества!"""
        else:
            response = "❌ Ошибка при создании пространства"
    else:
        response = "❌ Неверный тип пространства"
    
    await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
    
    # Очищаем временные данные
    context.user_data.pop('awaiting_space_name', None)
    context.user_data.pop('selected_space_type', None)
    context.user_data.pop('selected_space_type_name', None)

async def handle_join_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик присоединения к пространству"""
    user = update.effective_user
    
    if context.args:
        invite_code = context.args[0].upper()
        success, message = join_financial_space(invite_code, user.id, user.first_name)
        
        response = f"**{'✅' if success else '❌'} {message}**"
    else:
        # Если код отправлен как обычное сообщение
        text = update.message.text
        if len(text) == 8 and text.isalnum() and text.isupper():
            invite_code = text
            success, message = join_financial_space(invite_code, user.id, user.first_name)
            response = f"**{'✅' if success else '❌'} {message}**"
        else:
            response = """🔗 **Присоединение к пространству**

Отправьте код приглашения (8 символов, например: A1B2C3D4)

Или используйте команду:
`/join_space КОД_ПРИГЛАШЕНИЯ`

💡 **Совет:** Код приглашения выглядит примерно так: `A1B2C3D4`"""

    await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))

async def handle_my_spaces(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать пространства пользователя"""
    user = update.effective_user
    spaces_df = get_user_spaces(user.id)
    
    if spaces_df.empty:
        response = """🏠 **У вас пока нет финансовых пространств**

Создайте свое первое пространство или присоединитесь к существующему!

💡 **Советы:**
• 🏠 **Личное** - уже создано автоматически
• 👨‍👩‍👧‍👦 **Закрытая группа** - для семьи и друзей  
• 🌐 **Публичное сообщество** - для тематического общения"""
    else:
        response = "🏠 **Ваши финансовые пространства:**\n\n"
        
        for _, space in spaces_df.iterrows():
            # Эмодзи для типа пространства
            type_emoji = {
                'personal': '🏠',
                'private': '👨‍👩‍👧‍👦', 
                'public': '🌐'
            }.get(space['space_type'], '📁')
            
            # Эмодзи для роли
            role_emoji = "👑" if space['role'] == 'owner' else "👤" if space['role'] == 'admin' else "🙂"
            
            response += f"{type_emoji} **{space['name']}** {role_emoji}\n"
            response += f"   📝 {space['description'] or 'Без описания'}\n"
            
            if space['space_type'] != 'personal':
                response += f"   🔑 Код: `{space['invite_code']}`\n"
            
            response += f"   🆔 ID: {space['id']}\n\n"
        
        response += "💡 **Используйте:**\n"
        response += "• `/switch_space ID` - переключиться на пространство\n"
        response += "• `/space_members ID` - управление участниками\n"
        response += "• `/join_space КОД` - присоединиться к новому\n"
        response += "• Траты сохраняются в текущее активное пространство"

    await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))

async def handle_switch_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключение между пространствами"""
    user = update.effective_user
    
    if context.args:
        try:
            space_id = int(context.args[0])
            spaces_df = get_user_spaces(user.id)
            
            if space_id in spaces_df['id'].values:
                context.user_data['current_space'] = space_id
                space_info = spaces_df[spaces_df['id'] == space_id].iloc[0]
                space_name = space_info['name']
                space_type = space_info['space_type']
                
                type_names = {
                    'personal': 'личное пространство',
                    'private': 'закрытая группа', 
                    'public': 'публичное сообщество'
                }
                
                response = f"✅ **Переключено на {type_names.get(space_type, 'пространство')}:** {space_name}"
            else:
                response = "❌ У вас нет доступа к этому пространству"
        except ValueError:
            response = "❌ Неверный ID пространства"
    else:
        response = """🔄 **Переключение пространства**

Используйте команду:
`/switch_space ID_ПРОСТРАНСТВА`

ID пространства можно посмотреть в списке ваших пространств (/my_spaces).

💡 **По умолчанию** траты сохраняются в ваше личное пространство."""

    await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))

# ===== ОБРАБОТЧИКИ УПРАВЛЕНИЯ УЧАСТНИКАМИ =====
async def handle_space_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать участников пространства с управлением"""
    user = update.effective_user
    
    if context.args:
        try:
            space_id = int(context.args[0])
            spaces_df = get_user_spaces(user.id)
            
            if space_id not in spaces_df['id'].values:
                await update.message.reply_text(
                    "❌ У вас нет доступа к этому пространству",
                    reply_markup=get_main_keyboard(user.id)
                )
                return
            
            await show_space_members_details(update, context, space_id, user.id)
            
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный ID пространства",
                reply_markup=get_main_keyboard(user.id)
            )
    else:
        # Показываем список пространств для выбора
        spaces_df = get_user_spaces(user.id)
        
        if spaces_df.empty:
            await update.message.reply_text(
                "❌ У вас нет пространств для управления",
                reply_markup=get_main_keyboard(user.id)
            )
            return
        
        response = "👥 **Управление участниками**\n\nВыберите пространство:\n\n"
        
        for _, space in spaces_df.iterrows():
            members_count = len(get_space_members(space['id']))
            response += f"🆔 {space['id']} - **{space['name']}** ({members_count} участ.)\n"
        
        response += "\nИспользуйте: `/space_members ID_ПРОСТРАНСТВА`"
        
        await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))

async def show_space_members_details(update: Update, context: ContextTypes.DEFAULT_TYPE, space_id, user_id):
    """Показать детальную информацию об участниках пространства"""
    try:
        members_df = get_detailed_space_members(space_id)
        space_info_df = get_user_spaces(user_id)
        space_info = space_info_df[space_info_df['id'] == space_id].iloc[0]
        
        if members_df.empty:
            await update.message.reply_text(
                "❌ В пространстве нет участников",
                reply_markup=get_main_keyboard(user_id)
            )
            return
        
        response = f"👥 **Участники пространства**\n**{space_info['name']}**\n\n"
        
        # Получаем роль текущего пользователя
        current_user_role = members_df[members_df['user_id'] == user_id]['role'].iloc[0]
        can_manage = current_user_role in ['owner', 'admin']
        
        for _, member in members_df.iterrows():
            role_emoji = "👑" if member['role'] == 'owner' else "👤" if member['role'] == 'admin' else "🙂"
            join_date = datetime.strptime(str(member['joined_at']), '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
            
            response += f"{role_emoji} **{member['user_name']}**\n"
            response += f"   🆔 ID: {member['user_id']}\n"
            response += f"   📊 Роль: {member['role']}\n"
            response += f"   💸 Траты: {member['expense_count']} шт., {member['total_spent'] or 0:,.0f} руб\n"
            response += f"   📅 Вступил: {join_date}\n"
            
            # Кнопки управления (только для владельцев/админов и не для себя)
            if can_manage and member['user_id'] != user_id and member['role'] != 'owner':
                response += f"   🔧 Управление: "
                response += f"/remove_member_{space_id}_{member['user_id']} 🗑️"
                if member['role'] == 'member':
                    response += f" /make_admin_{space_id}_{member['user_id']} ⬆️"
                else:
                    response += f" /make_member_{space_id}_{member['user_id']} ⬇️"
                response += "\n"
            
            response += "\n"
        
        if can_manage:
            response += "**🔧 Команды управления:**\n"
            response += "• `/remove_member_ID_ПРОСТРАНСТВА_ID_УЧАСТНИКА` - удалить\n"
            response += "• `/make_admin_ID_ПРОСТРАНСТВА_ID_УЧАСТНИКА` - сделать админом\n"
            response += "• `/make_member_ID_ПРОСТРАНСТВА_ID_УЧАСТНИКА` - сделать участником\n"
        
        response += f"\n**🔗 Код приглашения:** `{space_info['invite_code']}`"
        
        # Кнопка для выхода из пространства
        if current_user_role != 'owner':
            response += f"\n\n🚪 Выйти: /leave_space_{space_id}"
        
        await update.message.reply_text(response, reply_markup=get_main_keyboard(user_id))
        
    except Exception as e:
        logger.error(f"❌ Ошибка показа участников: {e}")
        await update.message.reply_text(
            "❌ Ошибка при получении информации об участниках",
            reply_markup=get_main_keyboard(user_id)
        )

async def handle_remove_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик удаления участника"""
    user = update.effective_user
    text = update.message.text
    
    try:
        # Парсим команду: /remove_member_SPACEID_USERID
        parts = text.split('_')
        if len(parts) >= 4:
            space_id = int(parts[2])
            target_user_id = int(parts[3])
            
            success, message = remove_member_from_space(space_id, target_user_id, user.id)
            
            if success:
                # Получаем имя удаленного пользователя для красивого ответа
                members_df = get_detailed_space_members(space_id)
                target_user_name = "пользователь"
                if not members_df.empty:
                    target_user = members_df[members_df['user_id'] == target_user_id]
                    if not target_user.empty:
                        target_user_name = target_user.iloc[0]['user_name']
                
                response = f"✅ **Участник удален**\n\n👤 {target_user_name} был удален из пространства."
            else:
                response = f"❌ {message}"
        else:
            response = "❌ Неверный формат команды"
        
        await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
        
    except Exception as e:
        logger.error(f"❌ Ошибка удаления участника: {e}")
        await update.message.reply_text(
            "❌ Ошибка при удалении участника",
            reply_markup=get_main_keyboard(user.id)
        )

async def handle_change_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик изменения роли участника"""
    user = update.effective_user
    text = update.message.text
    
    try:
        # Парсим команду: /make_admin_SPACEID_USERID или /make_member_SPACEID_USERID
        parts = text.split('_')
        if len(parts) >= 4:
            command = parts[1]  # admin или member
            space_id = int(parts[2])
            target_user_id = int(parts[3])
            
            new_role = 'admin' if command == 'admin' else 'member'
            success, message = change_member_role(space_id, target_user_id, new_role, user.id)
            
            if success:
                role_name = "администратором" if new_role == 'admin' else "участником"
                response = f"✅ **Роль изменена**\n\nПользователь теперь {role_name}."
            else:
                response = f"❌ {message}"
        else:
            response = "❌ Неверный формат команды"
        
        await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
        
    except Exception as e:
        logger.error(f"❌ Ошибка изменения роли: {e}")
        await update.message.reply_text(
            "❌ Ошибка при изменении роли",
            reply_markup=get_main_keyboard(user.id)
        )

async def handle_leave_space(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выхода из пространства"""
    user = update.effective_user
    text = update.message.text
    
    try:
        # Парсим команду: /leave_space_SPACEID
        parts = text.split('_')
        if len(parts) >= 3:
            space_id = int(parts[2])
            
            success, message = leave_space(space_id, user.id)
            
            if success:
                response = f"✅ **Вы вышли из пространства**\n\n{message}"
                
                # Если пользователь вышел из текущего пространства - сбрасываем его
                if context.user_data.get('current_space') == space_id:
                    context.user_data.pop('current_space', None)
            else:
                response = f"❌ {message}"
        else:
            response = "❌ Неверный формат команды"
        
        await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
        
    except Exception as e:
        logger.error(f"❌ Ошибка выхода из пространства: {e}")
        await update.message.reply_text(
            "❌ Ошибка при выходе из пространства",
            reply_markup=get_main_keyboard(user.id)
        )

async def handle_invite_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о приглашении"""
    user = update.effective_user
    
    if context.args:
        try:
            space_id = int(context.args[0])
            spaces_df = get_user_spaces(user.id)
            
            if space_id not in spaces_df['id'].values:
                await update.message.reply_text(
                    "❌ У вас нет доступа к этому пространству",
                    reply_markup=get_main_keyboard(user.id)
                )
                return
            
            space_info = spaces_df[spaces_df['id'] == space_id].iloc[0]
            
            response = f"""🔗 **Приглашение в пространство**

📝 **Название:** {space_info['name']}
🔑 **Код приглашения:** `{space_info['invite_code']}`
👥 **Тип:** {get_space_type_display_name(space_info['space_type'])}

**Как пригласить:**
1. Отправьте код приглашения другу
2. Попросите его использовать команду:
   `/join_space {space_info['invite_code']}`
3. Или отправить код боту: `{space_info['invite_code']}`

💡 **Совет:** Код можно отправлять прямо в чат!"""

            await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
            
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный ID пространства",
                reply_markup=get_main_keyboard(user.id)
            )
    else:
        response = """🔗 **Приглашение участников**

Используйте команду:
`/invite_info ID_ПРОСТРАНСТВА`

Чтобы получить код приглашения и инструкции.

💡 **Совет:** Участники могут присоединиться к приватным пространствам только по коду приглашения!"""

        await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))

def get_space_type_display_name(space_type):
    """Получение отображаемого имени типа пространства"""
    names = {
        'personal': 'Личное',
        'private': 'Закрытая группа',
        'public': 'Публичное сообщество'
    }
    return names.get(space_type, space_type)

# ===== ОБРАБОТЧИК ТЕКСТА =====
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    logger.info(f"📨 Получено текстовое сообщение от {user.first_name}: {text}")
    
    # Обработка подтверждений
    if text in ["✅ Да, сохранить", "❌ Отменить"]:
        await handle_confirmation(update, context)
        return
    
    # Обработка выбора типа пространства
    if context.user_data.get('awaiting_space_creation'):
        await handle_space_type_selection(update, context)
        return
    
    # Обработка ввода названия пространства
    if context.user_data.get('awaiting_space_name'):
        await handle_space_name_input(update, context)
        return
    
    # Обработка кнопок главного меню
    if text == "📊 Статистика":
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
    # === НОВЫЕ КНОПКИ ПРОСТРАНСТВ ===
    elif text == "➕ Создать пространство":
        await handle_create_space(update, context)
    elif text == "🏠 Мои пространства":
        await handle_my_spaces(update, context)
    elif text == "👥 Управление участниками":
        await handle_space_members(update, context)
    elif text == "🔗 Пригласить":
        await handle_invite_info(update, context)
    elif text in ["🏠 Личное пространство", "👨‍👩‍👧‍👦 Закрытая группа (семья/друзья)", "🌐 Публичное сообщество", "❌ Отмена"]:
        await handle_space_type_selection(update, context)
    # === ОБРАБОТКА КОМАНД УПРАВЛЕНИЯ УЧАСТНИКАМИ ===
    elif text.startswith('/remove_member_'):
        await handle_remove_member(update, context)
    elif text.startswith('/make_admin_') or text.startswith('/make_member_'):
        await handle_change_role(update, context)
    elif text.startswith('/leave_space_'):
        await handle_leave_space(update, context)
    # === ОБРАБОТКА КОДОВ ПРИГЛАШЕНИЯ ===
    elif len(text) == 8 and text.isalnum() and text.isupper():
        # Если сообщение похоже на код приглашения
        await handle_join_space(update, context)
    else:
        # Попытка распознать текстовую трату
        try:
            parts = text.split()
            if len(parts) >= 2:
                amount = float(parts[0].replace(',', '.'))
                category = parts[1].lower()
                description = " ".join(parts[2:]) if len(parts) > 2 else ""
                
                # Получаем текущее пространство из контекста
                space_id = context.user_data.get('current_space')
                
                add_expense(user.id, user.first_name, amount, category, description, space_id)
                
                # Определяем название пространства для ответа
                spaces_df = get_user_spaces(user.id)
                if space_id and space_id in spaces_df['id'].values:
                    space_name = spaces_df[spaces_df['id'] == space_id]['name'].iloc[0]
                    space_info = f"в пространстве **{space_name}**"
                else:
                    space_info = "в **личном пространстве**"
                
                response = f"""✅ **Трата добавлена {space_info}!**

💁 **Кто:** {user.first_name}
💸 **Сумма:** {amount} руб
📂 **Категория:** {category}"""
                
                if description:
                    response += f"\n📝 **Описание:** {description}"
                    
                await update.message.reply_text(response, reply_markup=get_main_keyboard(user.id))
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
        space_id = context.user_data.get('current_space')
        
        # Получаем статистику для текущего пользователя и пространства
        if space_id:
            # Статистика для конкретного пространства
            if isinstance(conn, sqlite3.Connection):
                df = pd.read_sql_query(f'''
                    SELECT category, SUM(amount) as total, COUNT(*) as count 
                    FROM expenses 
                    WHERE user_id = {user_id} AND space_id = {space_id}
                    GROUP BY category 
                    ORDER BY total DESC
                ''', conn)
                
                # Получаем информацию о пространстве
                space_info = pd.read_sql_query(f'''
                    SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
                ''', conn).iloc[0]
            else:
                df = pd.read_sql_query(f'''
                    SELECT category, SUM(amount) as total, COUNT(*) as count 
                    FROM expenses 
                    WHERE user_id = {user_id} AND space_id = {space_id}
                    GROUP BY category 
                    ORDER BY total DESC
                ''', conn)
                
                space_info = pd.read_sql_query(f'''
                    SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
                ''', conn).iloc[0]
            
            space_name = space_info['name']
            space_type = space_info['space_type']
        else:
            # Статистика для личного пространства (по умолчанию)
            personal_space_id = ensure_user_has_personal_space(user_id, update.effective_user.first_name)
            
            if isinstance(conn, sqlite3.Connection):
                df = pd.read_sql_query(f'''
                    SELECT category, SUM(amount) as total, COUNT(*) as count 
                    FROM expenses 
                    WHERE user_id = {user_id} AND space_id = {personal_space_id}
                    GROUP BY category 
                    ORDER BY total DESC
                ''', conn)
            else:
                df = pd.read_sql_query(f'''
                    SELECT category, SUM(amount) as total, COUNT(*) as count 
                    FROM expenses 
                    WHERE user_id = {user_id} AND space_id = {personal_space_id}
                    GROUP BY category 
                    ORDER BY total DESC
                ''', conn)
            
            space_name = "Личное пространство"
            space_type = "personal"
        
        if df.empty:
            await update.message.reply_text(
                f"📊 В пространстве '{space_name}' пока нет данных для статистики.\n"
                "Добавьте первую трату! 💸",
                reply_markup=get_main_keyboard(user_id)
            )
            return
        
        # Создаем график
        plt.figure(figsize=(10, 6))
        plt.pie(df['total'], labels=df['category'], autopct='%1.1f%%')
        
        # Заголовок в зависимости от типа пространства
        type_emoji = {
            'personal': '🏠',
            'private': '👨‍👩‍👧‍👦',
            'public': '🌐'
        }.get(space_type, '📊')
        
        plt.title(f'{type_emoji} Распределение трат в "{space_name}"')
        
        # Сохраняем график
        chart_path = 'stats.png'
        plt.savefig(chart_path)
        plt.close()
        
        # Формируем текст статистики
        total_spent = df['total'].sum()
        stats_text = f"""📈 **СТАТИСТИКА {type_emoji} "{space_name.upper()}"**

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
                reply_markup=get_main_keyboard(user_id)
            )
        
        # Удаляем временный файл
        os.remove(chart_path)
        
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при формировании статистики: {str(e)}",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
    finally:
        conn.close()

async def show_monthly_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        user_id = update.effective_user.id
        space_id = context.user_data.get('current_space')
        current_month = datetime.now().strftime('%Y-%m')
        
        if not space_id:
            space_id = ensure_user_has_personal_space(user_id, update.effective_user.first_name)
        
        # Получаем информацию о пространстве
        if isinstance(conn, sqlite3.Connection):
            space_info = pd.read_sql_query(f'''
                SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
            ''', conn).iloc[0]
            
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total
                FROM expenses 
                WHERE user_id = {user_id} AND space_id = {space_id} AND strftime('%Y-%m', date) = '{current_month}'
                GROUP BY category 
                ORDER BY total DESC
            ''', conn)
        else:
            space_info = pd.read_sql_query(f'''
                SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
            ''', conn).iloc[0]
            
            df = pd.read_sql_query(f'''
                SELECT category, SUM(amount) as total
                FROM expenses 
                WHERE user_id = {user_id} AND space_id = {space_id} AND DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)
                GROUP BY category 
                ORDER BY total DESC
            ''', conn)
        
        if df.empty:
            space_name = space_info['name']
            await update.message.reply_text(
                f"📅 В пространстве '{space_name}' за текущий месяц пока нет трат.\n"
                "Добавьте первую трату этого месяца! 💸",
                reply_markup=get_main_keyboard(user_id)
            )
            return
        
        total_spent = df['total'].sum()
        
        type_emoji = {
            'personal': '🏠',
            'private': '👨‍👩‍👧‍👦',
            'public': '🌐'
        }.get(space_info['space_type'], '📅')
        
        stats_text = f"""{type_emoji} **СТАТИСТИКА ЗА ТЕКУЩИЙ МЕСЯЦ**
**Пространство:** {space_info['name']}

💰 **Всего потрачено:** {total_spent:,.0f} руб

**📋 Траты по категориям:**
"""
        
        for _, row in df.iterrows():
            percentage = (row['total'] / total_spent) * 100
            stats_text += f"• {row['category']}: {row['total']:,.0f} руб ({percentage:.1f}%)\n"
        
        await update.message.reply_text(stats_text, reply_markup=get_main_keyboard(user_id))
        
    except Exception as e:
        logger.error(f"❌ Ошибка месячной статистики: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при формировании статистики: {str(e)}",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
    finally:
        conn.close()

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        user_id = update.effective_user.id
        space_id = context.user_data.get('current_space')
        
        if not space_id:
            space_id = ensure_user_has_personal_space(user_id, update.effective_user.first_name)
        
        # Получаем информацию о пространстве
        if isinstance(conn, sqlite3.Connection):
            space_info = pd.read_sql_query(f'''
                SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
            ''', conn).iloc[0]
            
            df = pd.read_sql_query(f'''
                SELECT amount, category, description, date
                FROM expenses 
                WHERE user_id = {user_id} AND space_id = {space_id}
                ORDER BY date DESC 
                LIMIT 10
            ''', conn)
        else:
            space_info = pd.read_sql_query(f'''
                SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
            ''', conn).iloc[0]
            
            df = pd.read_sql_query(f'''
                SELECT amount, category, description, date
                FROM expenses 
                WHERE user_id = {user_id} AND space_id = {space_id}
                ORDER BY date DESC 
                LIMIT 10
            ''', conn)
        
        if df.empty:
            space_name = space_info['name']
            await update.message.reply_text(
                f"📝 В пространстве '{space_name}' пока нет добавленных трат.\n"
                "Добавьте первую трату! 💸",
                reply_markup=get_main_keyboard(user_id)
            )
            return
        
        type_emoji = {
            'personal': '🏠',
            'private': '👨‍👩‍👧‍👦',
            'public': '🌐'
        }.get(space_info['space_type'], '📝')
        
        list_text = f"""{type_emoji} **ПОСЛЕДНИЕ ТРАТЫ**
**Пространство:** {space_info['name']}

"""
        
        for _, row in df.iterrows():
            date = datetime.strptime(str(row['date']), '%Y-%m-%d %H:%M:%S').strftime('%d.%m %H:%M')
            list_text += f"💸 **{row['amount']} руб** - {row['category']}\n"
            
            if row['description']:
                list_text += f"   📋 {row['description']}\n"
            
            list_text += f"   📅 {date}\n\n"
        
        await update.message.reply_text(list_text, parse_mode='Markdown', reply_markup=get_main_keyboard(user_id))
        
    except Exception as e:
        logger.error(f"❌ Ошибка списка трат: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при получении списка трат: {str(e)}",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
    finally:
        conn.close()

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        user_id = update.effective_user.id
        space_id = context.user_data.get('current_space')
        
        if not space_id:
            space_id = ensure_user_has_personal_space(user_id, update.effective_user.first_name)
        
        # Получаем информацию о пространстве
        if isinstance(conn, sqlite3.Connection):
            space_info = pd.read_sql_query(f'''
                SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
            ''', conn).iloc[0]
            
            df = pd.read_sql_query(f'''
                SELECT date, amount, category, description
                FROM expenses 
                WHERE user_id = {user_id} AND space_id = {space_id}
                ORDER BY date DESC
            ''', conn)
        else:
            space_info = pd.read_sql_query(f'''
                SELECT name, space_type FROM financial_spaces WHERE id = {space_id}
            ''', conn).iloc[0]
            
            df = pd.read_sql_query(f'''
                SELECT date, amount, category, description
                FROM expenses 
                WHERE user_id = {user_id} AND space_id = {space_id}
                ORDER BY date DESC
            ''', conn)
        
        if df.empty:
            await update.message.reply_text(
                "📈 Нет данных для выгрузки.",
                reply_markup=get_main_keyboard(user_id)
            )
            return
        
        # Создаем Excel файл
        excel_path = 'expenses_export.xlsx'
        df.to_excel(excel_path, index=False)
        
        space_name = space_info['name'].replace(' ', '_')
        
        # Отправляем файл
        with open(excel_path, 'rb') as excel_file:
            await update.message.reply_document(
                document=excel_file,
                filename=f'expenses_{space_name}_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
                caption=f'📈 Выгрузка данных из пространства "{space_info["name"]}"',
                reply_markup=get_main_keyboard(user_id)
            )
        
        # Удаляем временный файл
        os.remove(excel_path)
        
    except Exception as e:
        logger.error(f"❌ Ошибка выгрузки в Excel: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при выгрузке данных: {str(e)}",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
    finally:
        conn.close()

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    try:
        conn = get_db_connection()
        space_id = context.user_data.get('current_space')
        
        if space_id:
            # Очистка данных в конкретном пространстве
            if isinstance(conn, sqlite3.Connection):
                c = conn.cursor()
                c.execute('DELETE FROM expenses WHERE user_id = ? AND space_id = ?', (user.id, space_id))
                
                # Получаем название пространства
                space_name = pd.read_sql_query(f'SELECT name FROM financial_spaces WHERE id = {space_id}', conn).iloc[0]['name']
            else:
                c = conn.cursor()
                c.execute('DELETE FROM expenses WHERE user_id = %s AND space_id = %s', (user.id, space_id))
                
                space_name = pd.read_sql_query(f'SELECT name FROM financial_spaces WHERE id = {space_id}', conn).iloc[0]['name']
            
            conn.commit()
            conn.close()
            
            await update.message.reply_text(
                f"✅ Данные в пространстве '{space_name}' успешно очищены!\n"
                "Начинаем с чистого листа 🎯",
                reply_markup=get_main_keyboard(user.id)
            )
        else:
            # Очистка всех данных пользователя
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
                reply_markup=get_main_keyboard(user.id)
            )
        
    except Exception as e:
        logger.error(f"❌ Ошибка очистки данных: {str(e)}")
        await update.message.reply_text(
            f"❌ Ошибка при очистке данных: {str(e)}",
            reply_markup=get_main_keyboard(user.id)
        )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    help_text = """
🆘 **ПОМОЩЬ ПО ИСПОЛЬЗОВАНИЮ**

🏠 **СИСТЕМА ПРОСТРАНСТВ:**
• 🏠 **Личное** - ваши приватные траты (создается автоматически)
• 👨‍👩‍👧‍👦 **Закрытые группы** - общие траты с семьей/друзьями
• 🌐 **Публичные сообщества** - анонимная статистика и сравнения

💸 **Добавить трату:**
• Используйте кнопку с формой - удобный калькулятор
• Или пишите текстом: `500 продукты` или `1500 кафе обед`
• Траты сохраняются в текущее активное пространство

🎤 **Голосовой ввод:**
• Отправьте голосовое сообщение с описанием траты
• Пример: "500 продукты хлеб молоко"

📸 **Распознавание чеков:**
• Отправьте фото чека для автоматического распознавания
• Бот найдет сумму и магазин

📊 **Статистика:**
• **Статистика** - по текущему пространству
• **Статистика за месяц** - анализ текущего месяца
• **Последние траты** - история операций

🔄 **Управление пространствами:**
• `/my_spaces` - список ваших пространств
• `/switch_space ID` - переключиться на другое пространство  
• `/join_space КОД` - присоединиться по коду приглашения
• `/create_space` - создать новое пространство

👥 **Управление участниками:**
• `/space_members ID` - просмотр и управление участниками
• `/invite_info ID` - получить код приглашения
• Владельцы могут: удалять участников, назначать админов
• Админы могут: удалять обычных участников

📈 **Excel** - полная выгрузка данных текущего пространства

🗑️ **Очистить данные** - удалить траты (в текущем пространстве или все)

**💡 Советы:**
• Используйте разные пространства для разных целей
• Регулярно проверяйте статистику
• Экспортируйте данные для ведения бюджета
• Приглашайте в закрытые группы только доверенных людей
• Владелец не может покинуть пространство пока в нем есть другие участники
"""
    await update.message.reply_text(help_text, reply_markup=get_main_keyboard(user.id))

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
    application.add_handler(CommandHandler("create_space", handle_create_space))
    application.add_handler(CommandHandler("join_space", handle_join_space))
    application.add_handler(CommandHandler("my_spaces", handle_my_spaces))
    application.add_handler(CommandHandler("switch_space", handle_switch_space))
    application.add_handler(CommandHandler("space_members", handle_space_members))
    application.add_handler(CommandHandler("invite_info", handle_invite_info))
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
