import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os
from bot import get_db_connection, ensure_user_has_personal_space, add_expense, create_personal_space
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

# Глобальная переменная для хранения экземпляра бота
bot_application = None

def get_main_keyboard():
    """Основная клавиатура для навигации в WebApp"""
    web_app_url = os.environ.get('RAILWAY_STATIC_URL', 'https://your-app.railway.app')
    
    keyboard = [
        [KeyboardButton("💸 Открыть приложение", web_app=WebAppInfo(url=web_app_url))],
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
• 💸 **Удобное добавление трат** через Web-приложение
• 🏠 **Управление пространствами** - личные, семейные, публичные
• 👥 **Совместные бюджеты** с друзьями и семьей  
• 📊 **Детальная аналитика** с графиками

💡 **Нажмите «💸 Открыть приложение» для доступа ко всем функциям!**

📱 **Быстрый ввод через бота:**
• Текстом: `500 продукты` или `1500 кафе обед`
"""
    
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрая статистика через бота"""
    try:
        user = update.effective_user
        space_id = ensure_user_has_personal_space(user.id, user.first_name)
        
        conn = get_db_connection()
        
        if isinstance(conn, conn.__class__.__name__ == 'sqlite3.Connection'):
            df = pd.read_sql_query(
                'SELECT category, SUM(amount) as total FROM expenses WHERE user_id = ? AND space_id = ? GROUP BY category ORDER BY total DESC LIMIT 5',
                conn, params=(user.id, space_id)
            )
        else:
            df = pd.read_sql_query(
                'SELECT category, SUM(amount) as total FROM expenses WHERE user_id = %s AND space_id = %s GROUP BY category ORDER BY total DESC LIMIT 5',
                conn, params=(user.id, space_id)
            )
        
        conn.close()
        
        if df.empty:
            await update.message.reply_text(
                "📊 Пока нет данных для статистики.\n\n"
                "💡 Откройте Web-приложение для детальной аналитики!",
                reply_markup=get_main_keyboard()
            )
            return
        
        total_spent = df['total'].sum()
        stats_text = f"📊 **Быстрая статистика**\n\n💰 **Всего потрачено:** {total_spent:,.0f} руб\n\n**Топ категории:**\n"
        
        for _, row in df.iterrows():
            percentage = (row['total'] / total_spent) * 100
            stats_text += f"• {row['category']}: {row['total']:,.0f} руб ({percentage:.1f}%)\n"
        
        stats_text += "\n💡 **Для детальной аналитики откройте Web-приложение!**"
        
        await update.message.reply_text(stats_text, reply_markup=get_main_keyboard())
        
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {str(e)}")
        await update.message.reply_text(
            "❌ Ошибка при формировании статистики. Попробуйте открыть Web-приложение.",
            reply_markup=get_main_keyboard()
        )

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Последние траты"""
    try:
        user = update.effective_user
        space_id = ensure_user_has_personal_space(user.id, user.first_name)
        
        conn = get_db_connection()
        
        if isinstance(conn, conn.__class__.__name__ == 'sqlite3.Connection'):
            df = pd.read_sql_query(
                'SELECT amount, category, description, date FROM expenses WHERE user_id = ? AND space_id = ? ORDER BY date DESC LIMIT 5',
                conn, params=(user.id, space_id)
            )
        else:
            df = pd.read_sql_query(
                'SELECT amount, category, description, date FROM expenses WHERE user_id = %s AND space_id = %s ORDER BY date DESC LIMIT 5',
                conn, params=(user.id, space_id)
            )
        
        conn.close()
        
        if df.empty:
            await update.message.reply_text(
                "📝 Пока нет добавленных трат.\n\n"
                "💡 Откройте Web-приложение для удобного добавления!",
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
        
        list_text += "💡 **Откройте Web-приложение для полной истории!**"
        
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
        
        if isinstance(conn, conn.__class__.__name__ == 'sqlite3.Connection'):
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
• **💸 Открыть приложение** - Полноценный Web-интерфейс со всеми функциями
• **📊 Статистика** - Быстрая статистика в чате
• **📝 Последние траты** - История операций

🚀 **Что можно в Web-приложении:**
• Удобное добавление трат с калькулятором
• Управление пространствами (личные, семейные, публичные)
• Приглашение участников в группы
• Детальная аналитика с графиками
• Просмотр участников и управление

🎯 **Быстрый ввод через бота:**
• Текстом: `500 продукты` или `1500 кафе обед`

💬 **Просто отправьте сумму и категорию текстом для быстрого добавления!**
"""
    await update.message.reply_text(help_text, reply_markup=get_main_keyboard())

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

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"❌ Ошибка: {context.error}", exc_info=context.error)

def setup_bot():
    """Настройка и запуск бота"""
    global bot_application
    
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("❌ TELEGRAM_BOT_TOKEN не найден в переменных окружения")
        return None
    
    try:
        application = Application.builder().token(bot_token).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        application.add_error_handler(error_handler)
        
        bot_application = application
        logger.info("✅ Бот настроен и готов к запуску")
        return application
        
    except Exception as e:
        logger.error(f"❌ Ошибка настройки бота: {e}")
        return None

def run_bot():
    """Запуск бота"""
    application = setup_bot()
    if application:
        logger.info("🤖 Запускаем бота...")
        application.run_polling()
    else:
        logger.error("❌ Не удалось запустить бота")