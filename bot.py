import os
import datetime
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow

# === НАЛАШТУВАННЯ ===
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("BOT_TOKEN", "ТВОЙ_ТОКЕН_ТУТ")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# === GOOGLE CALENDAR ===
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
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start_time.isoformat() + "Z",
            timeMax=end_time.isoformat() + "Z",
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
    await update.message.reply_text("Вітаю 💅 Я бот салону краси S3!\nЯк вас звати?")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Приємно! 😊 Вкажіть, будь ласка, ваш номер телефону:")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("На яку дату бажаєте записатись? (у форматі РРРР-ММ-ДД)")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(update.message.text, "%Y-%m-%d").date()
        context.user_data["date"] = date
        await update.message.reply_text("⏰ Вкажіть бажаний час (у форматі ГГ:ХХ):")
        return TIME
    except ValueError:
        await update.message.reply_text("❌ Формат дати неправильний. Введіть ще раз: РРРР-ММ-ДД")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        context.user_data["time"] = time
        service = get_calendar_service()
        date = context.user_data["date"]

        if not is_time_slot_available(service, date, time):
            await update.message.reply_text("⚠️ Цей час уже зайнятий. Спробуйте інший.")
            return TIME

        start_time = datetime.datetime.combine(date, time)
        end_time = start_time + datetime.timedelta(minutes=90)

        event = {
            "summary": f"💅 Запис у S3 Beauty Salon ({context.user_data['name']})",
            "description": f"Телефон: {context.user_data['phone']}",
            "start": {"dateTime": start_time.isoformat(), "timeZone": "Europe/Kyiv"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "Europe/Kyiv"},
        }
        service.events().insert(calendarId="primary", body=event).execute()

        await update.message.reply_text(
            f"✨ Запис підтверджено!\n\n"
            f"👩‍💼 Ім'я: {context.user_data['name']}\n"
            f"📞 Телефон: {context.user_data['phone']}\n"
            f"📅 Дата: {date.strftime('%d.%m.%Y')}\n"
            f"⏰ Час: {time.strftime('%H:%M')}\n\n"
            f"До зустрічі у салоні краси S3 💖"
        )
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("❌ Формат часу неправильний. Введіть ще раз: ГГ:ХХ")
        return TIME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано ❌")
    return ConversationHandler.END


# === FLASK APP ===
app = Flask(__name__)
application = Application.builder().token(TOKEN).updater(None).build()

@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "🤖 S3 Beauty Bot працює!"


# === HANDLERS ===
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


# === RUN ===
if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    import asyncio
    async def run():
        await application.initialize()
        await application.start()
        await application.bot.set_webhook(
            url=f"https://s3-beauty-bot.onrender.com/{TOKEN}"
        )
        app.run(host="0.0.0.0", port=PORT)
    asyncio.run(run())
