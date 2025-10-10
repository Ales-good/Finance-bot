import sqlite3
import pandas as pd
from datetime import datetime
import psycopg2
from urllib.parse import urlparse
import logging
from flask import Flask, request, jsonify
import random
import string
import os

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
        
        c.execute('''CREATE TABLE IF NOT EXISTS financial_spaces
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT NOT NULL,
                      description TEXT,
                      space_type TEXT DEFAULT 'personal',
                      created_by INTEGER,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      invite_code TEXT UNIQUE,
                      is_active BOOLEAN DEFAULT TRUE)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS space_members
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      space_id INTEGER,
                      user_id INTEGER,
                      user_name TEXT,
                      role TEXT DEFAULT 'member',
                      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (space_id) REFERENCES financial_spaces (id))''')
        
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
        import json
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
def index():
    return jsonify({"status": "OK", "message": "Finance Bot API работает"})

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
            else:
                query = '''SELECT category, SUM(amount) as total, COUNT(*) as count
                           FROM expenses 
                           WHERE space_id = %s AND user_id = %s
                           GROUP BY category 
                           ORDER BY total DESC'''
                df = pd.read_sql_query(query, conn, params=(space_id, user_id))
                
                count_query = '''SELECT COUNT(*) as total_count FROM expenses WHERE space_id = %s AND user_id = %s'''
                count_df = pd.read_sql_query(count_query, conn, params=(space_id, user_id))
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
                'count': int(row['count'])
            })
        
        return jsonify({
            'categories': categories,
            'total_count': int(count_df.iloc[0]['total_count']) if not count_df.empty else 0,
            'users': users
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

# Инициализация базы при импорте
init_db()
