import logging
import os
from bot_handlers import run_bot

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("🚀 Запускаем Telegram бота...")
    run_bot()