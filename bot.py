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
import asyncio

# CONFIG
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")

PORT = int(os.getenv("PORT", 10000))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "s3-beauty-bot.onrender.com")
WEBHOOK_URL = f"https://{RENDER_URL}/{TOKEN}"

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
CALENDAR_ID = "primary"

# LOGGING
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FLASK
app = Flask(__name__)

# GOOGLE CALENDAR
def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        if not os.path.exists(CREDENTIALS_FILE):
            raise RuntimeError("credentials.json not found. Place your Google client_secret file as credentials.json")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

# TELEGRAM HANDLERS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💅 Записатися", callback_data="book")],
        [InlineKeyboardButton("ℹ️ Про нас", callback_data="info")],
    ]
    await update.message.reply_text(
        "Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "book":
        await q.message.reply_text("Напишіть, будь ласка, ім'я:")
        context.user_data["flow"] = "name"
    elif q.data == "info":
        await q.message.reply_text("S3 Beauty — естетика в кожній деталі. Ми пропонуємо повний спектр послуг.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    flow = context.user_data.get("flow")

    if flow == "name":
        context.user_data["name"] = text
        context.user_data["flow"] = "phone"
        await update.message.reply_text("Дякую! Вкажіть, будь ласка, номер телефону:")
        return

    if flow == "phone":
        context.user_data["phone"] = text
        context.user_data["flow"] = "date"
        await update.message.reply_text("На яку дату бажаєте записатись? (формат YYYY-MM-DD)")
        return

    if flow == "date":
        try:
            d = datetime.datetime.strptime(text, "%Y-%m-%d").date()
            context.user_data["date"] = d
            context.user_data["flow"] = "time"
            await update.message.reply_text("Оберіть час (HH:MM):")
        except ValueError:
            await update.message.reply_text("Невірний формат дати. Спробуйте YYYY-MM-DD.")
        return

    if flow == "time":
        try:
            t = datetime.datetime.strptime(text, "%H:%M").time()
            context.user_data["time"] = t

            # Перевірка і створення події
            try:
                service = get_calendar_service()
            except Exception as e:
                logger.error("Google auth error: %s", e)
                await update.message.reply_text("Потрібна авторизація Google Calendar. Запустіть бот локально і пройдіть авторизацію.")
                return

            start_dt = datetime.datetime.combine(context.user_data["date"], context.user_data["time"])
            end_dt = start_dt + datetime.timedelta(minutes=90)

            # Перевірка вільного часу
            events = service.events().list(
                calendarId=CALENDAR_ID,
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True,
                orderBy="startTime"
            ).execute()

            if events.get("items"):
                await update.message.reply_text("⚠️ На цей час вже є запис. Спробуйте інший час.")
                return

            event = {
                "summary": f"S3 Booking — {context.user_data.get('name')}",
                "description": f"Tel: {context.user_data.get('phone')}",
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Kyiv"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Kyiv"},
            }
            service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

            await update.message.reply_text(
                f"✅ Ваш запис підтверджено!\n\nІм'я: {context.user_data.get('name')}\nТел: {context.user_data.get('phone')}\nДата: {start_dt.strftime('%d.%m.%Y')}\nЧас: {start_dt.strftime('%H:%M')} — {end_dt.strftime('%H:%M')}"
            )
            context.user_data.clear()

        except ValueError:
            await update.message.reply_text("Невірний формат часу. Введіть HH:MM.")
        return

    # default
    await update.message.reply_text("Скористайтесь /start або натисніть кнопку 'Записатися'.")

async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Ok")

# TELEGRAM APP
telegram_app = Application.builder().token(TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(handle_buttons))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# FLASK webhook route
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, telegram_app.bot)
    telegram_app.create_task(telegram_app.process_update(update))
    return "ok"

@app.route("/", methods=["GET"])
def index():
    return "S3 Beauty Bot is running."

# START
if __name__ == "__main__":
    async def sethook_and_run():
        await telegram_app.bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook set to %s", WEBHOOK_URL)
        # flask runs in main thread
        app.run(host="0.0.0.0", port=PORT)

    asyncio.run(sethook_and_run())
