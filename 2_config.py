"""
Конфигурация для Telegram бота рассылки
Все учетные данные загружаются из переменных окружения (.env файла)
"""

import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# SMTP конфигурация для Gmail
SMTP_CONFIG = {
    'server': os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
    'port': int(os.getenv('SMTP_PORT', '587')),
    'email': os.getenv('SMTP_EMAIL'),
    'password': os.getenv('SMTP_PASSWORD')
}

# Директория для временных файлов
DATA_DIR = os.getenv('DATA_DIR', 'user_data')

# Файл логирования
LOG_FILE = os.getenv('LOG_FILE', 'email_bot.log')

# Токен Telegram бота (из .env файла)
BOT_TOKEN = os.getenv('BOT_TOKEN')


def validate_config():
    """Проверка наличия всех необходимых переменных окружения"""
    required_vars = ['BOT_TOKEN', 'SMTP_EMAIL', 'SMTP_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        raise ValueError(
            f"❌ ОШИБКА КОНФИГУРАЦИИ!\n"
            f"Отсутствуют переменные окружения: {', '.join(missing_vars)}\n\n"
            f"Решение:\n"
            f"1. Создайте файл .env в корневой директории проекта\n"
            f"2. Скопируйте содержимое из .env.example\n"
            f"3. Заполните реальные значения\n"
            f"4. Запустите бота снова"
        )


# Проверяем конфиг при импорте
if __name__ != '__main__':
    try:
        validate_config()
    except ValueError as e:
        print(f"⚠️ {e}")
        raise
