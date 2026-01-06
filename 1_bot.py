import os
import logging
import pandas as pd
import smtplib
import sys
import asyncio
import warnings
import time
import re
from logging.handlers import RotatingFileHandler
from telegram.warnings import PTBUserWarning
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler
)
from config import SMTP_CONFIG, DATA_DIR, LOG_FILE, BOT_TOKEN

# Игнорируем предупреждения PTB
warnings.filterwarnings("ignore", category=PTBUserWarning)

# Исправление для Windows
if sys.version_info[0] == 3 and sys.version_info[1] >= 8 and sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Константы
EMAIL_REGEX = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_CONTACTS = 10000
SEND_DELAY = 0.5  # сек

# Состояния для ConversationHandler
UPLOAD_EXCEL, UPLOAD_ATTACHMENT, INPUT_SUBJECT, INPUT_BODY, CONFIRMATION = range(5)

# Логирование
logging.basicConfig(
    filename=LOG_FILE,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Добавляем RotatingFileHandler для ротации логов
handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# Создаем директорию для данных
os.makedirs(DATA_DIR, exist_ok=True)
logger.info(f"📁 Директория данных: {DATA_DIR}")


class EmailBot:
    def __init__(self):
        self.user_data = {}

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запуск бота"""
        user = update.effective_user
        await update.message.reply_text(
            f"Привет, {user.first_name}! 👋\n\n"
            f"🤖 Я бот для автоматизации рассылки коммерческих предложений.\n"
            f"📤 Отправка будет с адреса: {SMTP_CONFIG['email']}\n\n"
            f"Команды:\n"
            f"/send - начать рассылку\n"
            f"/cancel - отменить\n"
            f"/help - справка"
        )

    async def send_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начало рассылки"""
        await update.message.reply_text(
            "📊 ЗАГРУЖАЕМ КОНТАКТЫ\n\n"
            "Отправь Excel-файл с данными получателей.\n"
            "Обязательна колонка 'Email'.\n"
            "Максимум 50 MB, 10000 контактов."
        )
        return UPLOAD_EXCEL

    async def handle_excel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка Excel файла"""
        user_id = update.message.from_user.id
        logger.info(f"👤 Пользователь {user_id} загрузил Excel файл")

        try:
            # Получаем имя файла и расширение
            filename = update.message.document.filename
            file_ext = os.path.splitext(filename)[1].lower()

            logger.info(f"📁 Получен файл: {filename}, расширение: {file_ext}")

            # Проверяем расширение
            if file_ext not in ['.xlsx', '.xls']:
                await update.message.reply_text(
                    "❌ Неподдерживаемый формат файла.\n"
                    "Загрузите файл в формате Excel (.xlsx или .xls)."
                )
                return UPLOAD_EXCEL

            # Создаем директорию пользователя
            user_dir = os.path.join(DATA_DIR, f"user_{user_id}_{int(time.time())}")
            os.makedirs(user_dir, exist_ok=True)

            # Сохраняем файл
            excel_path = os.path.join(user_dir, filename)
            file = await update.message.document.get_file()
            await file.download_to_drive(excel_path)
            logger.info(f"💾 Файл сохранен: {excel_path}")

            # Проверяем файл
            try:
                engine = 'openpyxl' if file_ext == '.xlsx' else None
                df = pd.read_excel(excel_path, engine=engine)

                if 'Email' not in df.columns:
                    await update.message.reply_text(
                        "❌ В файле отсутствует колонка 'Email'.\n"
                        "Добавьте её и попробуйте снова."
                    )
                    return UPLOAD_EXCEL

                # Сохраняем данные пользователя
                self.user_data[user_id] = {
                    'excel_path': excel_path,
                    'user_dir': user_dir,
                    'contacts_count': len(df),
                    'filename': filename
                }

                await update.message.reply_text(
                    f"✅ Файл получен!\n\n"
                    f"👥 Контактов: {len(df)}\n\n"
                    f"📎 Теперь загрузи файл предложения\n"
                    f"(PDF, DOC, DOCX и т.д.)"
                )
                return UPLOAD_ATTACHMENT

            except Exception as e:
                logger.exception(f"❌ Ошибка при чтении Excel: {str(e)}")
                await update.message.reply_text(
                    f"❌ Ошибка при чтении файла.\n"
                    f"Убедитесь, что это правильный Excel файл."
                )
                return UPLOAD_EXCEL

        except Exception as e:
            logger.exception(f"❌ Ошибка загрузки файла: {str(e)}")
            await update.message.reply_text(
                "❌ Ошибка загрузки файла.\n"
                "Попробуйте снова."
            )
            return UPLOAD_EXCEL

    async def handle_attachment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка вложения"""
        user_id = update.message.from_user.id

        if user_id not in self.user_data:
            await update.message.reply_text(
                "❌ Сначала загрузите Excel-файл.\n"
                "Команда: /send"
            )
            return ConversationHandler.END

        try:
            file = await update.message.document.get_file()
            filename = update.message.document.filename
            attachment_path = os.path.join(self.user_data[user_id]['user_dir'], filename)

            await file.download_to_drive(attachment_path)
            self.user_data[user_id]['attachment_path'] = attachment_path
            self.user_data[user_id]['attachment_name'] = filename

            await update.message.reply_text("✉️ Введи тему письма:")
            return INPUT_SUBJECT

        except Exception as e:
            logger.exception(f"❌ Ошибка загрузки вложения: {str(e)}")
            await update.message.reply_text(
                "❌ Ошибка загрузки файла.\n"
                "Попробуйте снова."
            )
            return UPLOAD_ATTACHMENT

    async def handle_subject(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка темы письма"""
        user_id = update.message.from_user.id
        self.user_data[user_id]['subject'] = update.message.text

        await update.message.reply_text(
            "📝 Введи текст письма.\n\n"
            "Для персонализации используй {Name}, {Company} и т.д."
        )
        return INPUT_BODY

    async def handle_body(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текста письма"""
        user_id = update.message.from_user.id
        self.user_data[user_id]['body'] = update.message.text

        # Создаем сводку
        data = self.user_data[user_id]
        summary = (
            f"📋 ПРОВЕРЬ ДАННЫЕ:\n\n"
            f"📁 Файл: {data['filename']}\n"
            f"👥 Контактов: {data['contacts_count']}\n"
            f"📎 Вложение: {data['attachment_name']}\n"
            f"✉️ Тема: {data['subject'][:50]}...\n"
            f"📝 Текст: {data['body'][:100]}...\n\n"
            f"📧 Отправка с: {SMTP_CONFIG['email']}\n"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ ОТПРАВИТЬ", callback_data="confirm_send"),
                InlineKeyboardButton("❌ Отменить", callback_data="cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(summary, reply_markup=reply_markup)
        return CONFIRMATION

    async def handle_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка подтверждения"""
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id

        if query.data == "cancel":
            await query.edit_message_text("❌ Отменено")
            self.cleanup(user_id)
            return ConversationHandler.END

        if query.data == "confirm_send":
            await query.edit_message_text("⏳ Отправляю письма...\nПожалуйста, ждите...")

            try:
                result = await self.send_emails(user_id)
                await query.message.reply_text(result)
            except Exception as e:
                logger.exception(f"Ошибка отправки: {str(e)}")
                await query.message.reply_text(f"❌ Ошибка: {str(e)}")
            finally:
                self.cleanup(user_id)

            return ConversationHandler.END

    async def send_emails(self, user_id):
        """Отправка писем"""
        data = self.user_data[user_id]

        try:
            # Читаем Excel
            file_ext = os.path.splitext(data['excel_path'])[1].lower()
            engine = 'openpyxl' if file_ext == '.xlsx' else None
            df = pd.read_excel(data['excel_path'], engine=engine)

            success_count = 0
            error_count = 0
            error_emails = []

            # Подключаемся к SMTP
            try:
                server = smtplib.SMTP(
                    SMTP_CONFIG['server'],
                    SMTP_CONFIG['port'],
                    timeout=10
                )
                server.starttls(timeout=10)
                server.login(SMTP_CONFIG['email'], SMTP_CONFIG['password'])
                logger.info("✅ Подключено к SMTP")
            except Exception as e:
                logger.error(f"❌ Ошибка подключения SMTP: {str(e)}")
                return f"❌ Ошибка подключения к серверу: {str(e)}"

            # Отправляем письма
            for index, row in df.iterrows():
                email = row.get('Email', '')

                # Валидация email
                if not email or not re.match(EMAIL_REGEX, email):
                    error_count += 1
                    error_emails.append(email or "пусто")
                    continue

                try:
                    msg = self.prepare_email(row, data)
                    server.sendmail(SMTP_CONFIG['email'], email, msg.as_string())
                    success_count += 1
                    logger.info(f"✅ Письмо отправлено: {email}")
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки на {email}: {str(e)}")
                    error_count += 1
                    error_emails.append(email)

                # Пауза между письмами
                time.sleep(SEND_DELAY)

            server.quit()

            # Формируем отчет
            report = (
                f"✅ РАССЫЛКА ЗАВЕРШЕНА!\n\n"
                f"👥 Всего: {len(df)}\n"
                f"✅ Успешно: {success_count}\n"
                f"❌ Ошибок: {error_count}"
            )

            if error_count > 0:
                report += f"\n\n❌ Невалидные email ({min(error_count, 5)}):\n"
                report += "\n".join([f"  • {e}" for e in error_emails[:5]])
                if error_count > 5:
                    report += f"\n... и еще {error_count - 5}"

            logger.info(report)
            return report

        except Exception as e:
            logger.exception(f"❌ Ошибка: {str(e)}")
            return f"❌ Ошибка: {str(e)}"

    def prepare_email(self, row, data):
        """Подготовка письма"""
        msg = MIMEMultipart()
        msg['From'] = SMTP_CONFIG['email']
        msg['To'] = row.get('Email', '')
        msg['Subject'] = data['subject']

        # Подставляем переменные
        try:
            body = data['body'].format(**row.to_dict())
        except KeyError as e:
            logger.warning(f"⚠️ Отсутствует колонка для персонализации: {str(e)}")
            body = data['body']
        except Exception as e:
            logger.error(f"❌ Ошибка персонализации: {str(e)}")
            body = data['body']

        msg.attach(MIMEText(body, 'plain'))

        # Добавляем вложение
        try:
            with open(data['attachment_path'], 'rb') as attachment:
                part = MIMEApplication(attachment.read())
                part.add_header('Content-Disposition', f'attachment', filename=data['attachment_name'])
                msg.attach(part)
        except Exception as e:
            logger.error(f"❌ Ошибка вложения: {str(e)}")

        return msg

    def cleanup(self, user_id):
        """Очистка файлов пользователя"""
        if user_id in self.user_data:
            try:
                user_dir = self.user_data[user_id].get('user_dir')
                if os.path.exists(user_dir):
                    for file in os.listdir(user_dir):
                        try:
                            os.remove(os.path.join(user_dir, file))
                        except:
                            pass
                    os.rmdir(user_dir)
            except Exception as e:
                logger.error(f"❌ Ошибка очистки: {str(e)}")
            finally:
                del self.user_data[user_id]

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена"""
        user_id = update.message.from_user.id
        self.cleanup(user_id)
        await update.message.reply_text("❌ Отменено")
        return ConversationHandler.END

    def get_handlers(self):
        """Получить обработчики"""
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("send", self.send_command)],
            states={
                UPLOAD_EXCEL: [
                    MessageHandler(
                        filters.Document.MimeType("application/vnd.ms-excel") |
                        filters.Document.MimeType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") |
                        filters.Document.FileExtension("xlsx") |
                        filters.Document.FileExtension("xls") |
                        filters.Document.FileExtension("XLSX") |
                        filters.Document.FileExtension("XLS"),
                        self.handle_excel
                    ),
                    CommandHandler("cancel", self.cancel)
                ],
                UPLOAD_ATTACHMENT: [
                    MessageHandler(filters.Document.ALL, self.handle_attachment),
                    CommandHandler("cancel", self.cancel)
                ],
                INPUT_SUBJECT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_subject),
                    CommandHandler("cancel", self.cancel)
                ],
                INPUT_BODY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_body),
                    CommandHandler("cancel", self.cancel)
                ],
                CONFIRMATION: [
                    CallbackQueryHandler(self.handle_confirmation)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )

        return [
            CommandHandler("start", self.start),
            conv_handler
        ]


def main():
    """Главная функция"""
    logger.info("=" * 60)
    logger.info("🤖 ЗАПУСК БОТА")
    logger.info("=" * 60)
    logger.info(f"📁 Директория данных: {DATA_DIR}")
    logger.info(f"📧 SMTP сервер: {SMTP_CONFIG['server']}:{SMTP_CONFIG['port']}")
    logger.info(f"📤 Email отправителя: {SMTP_CONFIG['email']}")
    logger.info("=" * 60)

    bot = EmailBot()
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    for handler in bot.get_handlers():
        application.add_handler(handler)

    logger.info("✅ Бот готов к работе. Слушаю сообщения...")
    application.run_polling()


if __name__ == "__main__":
    main()
