import os
import datetime
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# --- Константи станів
NAME, PHONE, DATE, TIME = range(4)

# --- Flask сервер
app = Flask(__name__)

# --- Отримуємо токен із змінної середовища (Render)
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)

# --- Google Calendar API
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CALENDAR_ID = "primary"

def get_calendar_service():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("calendar", "v3", credentials=creds)

# --- Перевірка доступності часу
def is_time_slot_available(service, date, time):
    start = datetime.datetime.combine(date, time)
    end = start + datetime.timedelta(minutes=90)
    events = (
        service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=start.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return not events.get("items", [])

# --- Обробники
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Чудово! 📞 Вкажіть, будь ласка, ваш номер телефону:")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("📅 На яку дату бажаєте записатись? (у форматі РРРР-ММ-ДД)")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["date"] = datetime.datetime.strptime(update.message.text, "%Y-%m-%d").date()
        await update.message.reply_text("⏰ Вкажіть бажаний час (наприклад, 14:30):")
        return TIME
    except ValueError:
        await update.message.reply_text("⚠️ Невірний формат. Введіть дату у форматі РРРР-ММ-ДД.")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        date = context.user_data["date"]
        name = context.user_data["name"]
        phone = context.user_data["phone"]

        service = get_calendar_service()
        if not is_time_slot_available(service, date, time):
            await update.message.reply_text("⏰ На цей час уже є запис. Спробуйте інший час.")
            return TIME

        start_time = datetime.datetime.combine(date, time)
        end_time = start_time + datetime.timedelta(minutes=90)

        event = {
            "summary": f"Запис: {name}",
            "description": f"Телефон: {phone}",
            "start": {"dateTime": start_time.isoformat(), "timeZone": "Europe/Kiev"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "Europe/Kiev"},
        }

        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

        await update.message.reply_text(
            f"✨ {name}, дякуємо за запис!\n\n"
            f"📅 Дата: {date}\n"
            f"🕒 Час: {time.strftime('%H:%M')} – {end_time.strftime('%H:%M')}\n"
            f"📞 Телефон: {phone}\n\n"
            f"💅 До зустрічі в салоні краси S3!"
        )
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("⚠️ Невірний формат часу. Введіть, наприклад, 14:30.")
        return TIME

# --- Головна функція Telegram
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.update_queue.put_nowait(update)
    return "ok", 200

# --- Root (для Render перевірки)
@app.route("/")
def index():
    return "Bot is running!", 200

# --- Старт
if __name__ == "__main__":
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        },
        fallbacks=[],
    )

    application.add_handler(conv_handler)

    # Flask запускається на Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
