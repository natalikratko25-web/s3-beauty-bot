import os
import logging
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ================= CONFIG =================
TOKEN = os.getenv("8302341867:AAHd_faDWIBnC01wPdtoER75YaUb_gngdE0") 
PORT = int(os.environ.get("PORT", 10000))
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Flask app
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= TELEGRAM HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    text = (
        "Привіт 💅! Я — бот салону краси *S3Beauty*.\n\n"
        "Я допоможу вам записатись на процедуру, переглянути графік "
        "або отримати нагадування.\n\n"
        "Доступні команди:\n"
        "/start — почати спілкування\n"
        "/add — створити запис у календарі Google\n"
        "/help — довідка"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    await update.message.reply_text("Напишіть /add, щоб створити подію у календарі ✨")

async def add_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Додати подію в Google Calendar"""
    try:
        # Авторизація Google
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        service = build("calendar", "v3", credentials=creds)

        event = {
            "summary": "Запис до S3Beauty 💅",
            "description": "Автоматичний запис із Telegram",
            "start": {"dateTime": "2025-11-03T12:00:00+02:00"},
            "end": {"dateTime": "2025-11-03T13:00:00+02:00"},
        }

        event_result = service.events().insert(calendarId="primary", body=event).execute()
        await update.message.reply_text(f"✅ Подію створено: {event_result.get('htmlLink')}")

    except FileNotFoundError:
        await update.message.reply_text("⚠️ Не знайдено файл token.json. Спочатку створіть його через get_token.py.")
    except Exception as e:
        logger.error(f"Google API error: {e}")
        await update.message.reply_text("⚠️ Помилка при створенні події. Перевірте токен Google.")

async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Відповідь на довільні повідомлення"""
    await update.message.reply_text("Я поки не знаю цієї команди 😅\nВикористайте /help для довідки.")

# ================= FLASK ENDPOINTS =================

@app.route("/")
def index():
    return "Bot is running ✅"

@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    """Обробка запитів від Telegram"""
    data = request.get_json(force=True)
    update = Update.de_json(data, app.bot)
    await app.application.update_queue.put(update)
    return "ok", 200

# ================= TELEGRAM APP =================

application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("add", add_event))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message))

# Прив’язуємо Telegram до Flask
app.bot = application.bot
app.application = application

# ================= MAIN =================

if __name__ == "__main__":
    async def main():
        """Головна функція — запускає вебхук і сервер Flask"""
        render_url = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if not render_url:
            raise RuntimeError("❌ Не знайдено змінну середовища RENDER_EXTERNAL_HOSTNAME!")

        webhook_url = f"https://{render_url}/{TOKEN}"

        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook встановлено: {webhook_url}")

        app.run(host="0.0.0.0", port=PORT)

    asyncio.run(main())
