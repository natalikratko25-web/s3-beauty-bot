import os
import datetime
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow

# === CONFIG ===
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("BOT_TOKEN", "8302341867:AAHd_faDWIBnC01wPdtoER75YaUb_gngdE0")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# === Google Calendar ===
def get_calendar_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def is_time_slot_available(service, date, time):
    start_time = datetime.datetime.combine(date, time)
    end_time = start_time + datetime.timedelta(minutes=90)
    events = service.events().list(
        calendarId="primary",
        timeMin=start_time.isoformat() + "Z",
        timeMax=end_time.isoformat() + "Z",
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return not events.get("items", [])

# === Conversation states ===
NAME, PHONE, DATE, TIME = range(4)

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💅 Вітаю! Я бот салону краси S3.\nЯк вас звати?")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Приємно познайомитись! 😊 Вкажіть ваш номер телефону:")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("На яку дату бажаєте записатись? (формат YYYY-MM-DD)")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(update.message.text, "%Y-%m-%d").date()
        context.user_data["date"] = date
        await update.message.reply_text("⏰ Вкажіть бажаний час (HH:MM):")
        return TIME
    except ValueError:
        await update.message.reply_text("❌ Невірний формат дати. Введіть у форматі YYYY-MM-DD.")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        context.user_data["time"] = time
        service = get_calendar_service()
        date = context.user_data["date"]

        if not is_time_slot_available(service, date, time):
            await update.message.reply_text("⚠️ Цей час зайнятий. Оберіть інший.")
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
            f"👩 Ім'я: {context.user_data['name']}\n"
            f"📞 Телефон: {context.user_data['phone']}\n"
            f"📅 Дата: {date}\n"
            f"⏰ Час: {time.strftime('%H:%M')} - {(end_time.time()).strftime('%H:%M')}\n\n"
            f"До зустрічі в S3 Beauty 💖"
        )
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("❌ Невірний формат часу. Вкажіть HH:MM.")
        return TIME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано ❌")
    return ConversationHandler.END

# === Telegram application ===
application = Application.builder().token(TOKEN).build()
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
application.add_handler(conv_handler)

# === Flask webhook server ===
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "🤖 S3 Beauty Bot працює!"

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok", 200

# === Start Flask server ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
