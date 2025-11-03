import os
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import datetime

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN", "8302341867:AAHd_faDWIBnC01wPdtoER75YaUb_gngdE0")
PORT = int(os.getenv("PORT", 10000))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "s3-beauty-bot.onrender.com")
WEBHOOK_URL = f"https://{RENDER_URL}/{TOKEN}"

SCOPES = ['https://www.googleapis.com/auth/calendar']
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
CALENDAR_ID = "primary"

# === LOGGING ===
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# === FLASK APP ===
app = Flask(__name__)


# === GOOGLE CALENDAR ===
def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)


# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💅 Записатися", callback_data="book")],
        [InlineKeyboardButton("ℹ️ Про нас", callback_data="info")],
    ]
    await update.message.reply_text(
        "Вітаю 💖 Я — бот салону *S3 Beauty*! Обери дію нижче 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "book":
        await query.message.reply_text("Напишіть дату і час (наприклад: 5 листопада, 15:00)")
        context.user_data["step"] = "waiting_datetime"

    elif query.data == "info":
        await query.message.reply_text("💅 Салон S3 Beauty — професійні послуги манікюру та педикюру 💖")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("step") == "waiting_datetime":
        context.user_data["datetime"] = update.message.text
        keyboard = [
            [InlineKeyboardButton("✅ Так", callback_data="confirm_booking")],
            [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_booking")],
        ]
        await update.message.reply_text(
            f"Ви хочете записатися на {update.message.text}? Підтвердити?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["step"] = "confirming"
    else:
        await update.message.reply_text("Скористайся командою /start 💅")


async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dt = context.user_data.get("datetime", "невідомо")

    try:
        service = get_calendar_service()
        start_time = datetime.datetime.now() + datetime.timedelta(days=1)
        end_time = start_time + datetime.timedelta(hours=1)
        event = {
            'summary': f'Запис клієнта (Telegram)',
            'description': f'Час: {dt}',
            'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Europe/Kyiv'},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Europe/Kyiv'},
        }
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        await query.message.reply_text("✅ Запис підтверджено! До зустрічі 💅")
    except Exception as e:
        logger.error(f"Calendar error: {e}")
        await query.message.reply_text("⚠️ Сталася помилка при записі.")
    finally:
        context.user_data.clear()


async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("❌ Запис скасовано.")
    context.user_data.clear()


# === MAIN TELEGRAM APP ===
telegram_app = Application.builder().token(TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(handle_buttons, pattern="^(book|info)$"))
telegram_app.add_handler(CallbackQueryHandler(confirm_booking, pattern="confirm_booking"))
telegram_app.add_handler(CallbackQueryHandler(cancel_booking, pattern="cancel_booking"))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))


# === FLASK ROUTES ===
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, telegram_app.bot)
    telegram_app.create_task(telegram_app.process_update(update))
    return "ok"


@app.route("/", methods=["GET"])
def index():
    return "✅ S3 Beauty bot is running!"


# === STARTUP ===
if __name__ == "__main__":
    import asyncio

    async def set_webhook():
        await telegram_app.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to {WEBHOOK_URL}")

    asyncio.run(set_webhook())
    app.run(host="0.0.0.0", port=PORT)
