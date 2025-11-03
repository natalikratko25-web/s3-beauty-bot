import os
import datetime
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# === ЛОГІНГ ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === КОНСТАНТИ ===
TOKEN = os.getenv("BOT_TOKEN", "8302341867:AAHd_faDWIBnC01wPdtoER75YaUb_gngdE0")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
PORT = int(os.environ.get("PORT", 10000))

# === Google Calendar ===
def get_calendar_service():
    if not os.path.exists("token.json"):
        logger.error("❌ Файл token.json відсутній. Створи його локально перед деплоєм.")
        raise FileNotFoundError("token.json missing")

    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("calendar", "v3", credentials=creds)


def is_time_slot_available(service, date, time):
    start_time = datetime.datetime.combine(date, time)
    end_time = start_time + datetime.timedelta(minutes=90)
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start_time.isoformat(),
            timeMax=end_time.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return not events_result.get("items", [])


# === СТАНИ РОЗМОВИ ===
NAME, PHONE, DATE, TIME = range(4)

# === ОБРОБНИКИ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💅 Вітаю! Я бот салону краси S3.\nЯк вас звати?")
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Приємно познайомитись 😊\nВаш номер телефону?")
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("На яку дату хочете записатись? (формат: РРРР-ММ-ДД)")
    return DATE


async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(update.message.text, "%Y-%m-%d").date()
        context.user_data["date"] = date
        await update.message.reply_text("⏰ Вкажіть час (ГГ:ХХ):")
        return TIME
    except ValueError:
        await update.message.reply_text("❌ Невірний формат дати. Приклад: 2025-11-05")
        return DATE


async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        context.user_data["time"] = time
        service = get_calendar_service()
        date = context.user_data["date"]

        if not is_time_slot_available(service, date, time):
            await update.message.reply_text("⚠️ Цей час зайнятий, оберіть інший.")
            return TIME

        start_time = datetime.datetime.combine(date, time)
        end_time = start_time + datetime.timedelta(minutes=90)

        event = {
            "summary": f"💅 Запис у S3 ({context.user_data['name']})",
            "description": f"Телефон: {context.user_data['phone']}",
            "start": {"dateTime": start_time.isoformat(), "timeZone": "Europe/Kyiv"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "Europe/Kyiv"},
        }
        service.events().insert(calendarId="primary", body=event).execute()

        await update.message.reply_text(
            f"✨ Запис підтверджено!\n\n"
            f"👩‍💼 {context.user_data['name']}\n"
            f"📞 {context.user_data['phone']}\n"
            f"📅 {date.strftime('%d.%m.%Y')}\n"
            f"⏰ {time.strftime('%H:%M')} – {(end_time.time()).strftime('%H:%M')}"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Формат часу має бути ГГ:ХХ.")
        return TIME


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано ❌")
    return ConversationHandler.END


# === ФУНКЦІЯ СТВОРЕННЯ ДОДАТКУ ===
def create_app():
    app = Flask(__name__)

    telegram_app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    telegram_app.add_handler(conv_handler)

    @app.route("/", methods=["GET"])
    def index():
        return "🤖 S3 Beauty Bot працює!"

    @app.route(f"/{TOKEN}", methods=["POST"])
    async def webhook():
        data = request.get_json(force=True)
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return "ok", 200

    return app, telegram_app


# === ЗАПУСК ===
if __name__ == "__main__":
    app, telegram_app = create_app()

    # Встановлення webhook
    import asyncio
    asyncio.run(
        telegram_app.bot.set_webhook(
            url=f"https://s3-beauty-bot.onrender.com/{TOKEN}"
        )
    )

    app.run(host="0.0.0.0", port=PORT)
