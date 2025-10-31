import os
import datetime
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

# --- Константи станів ---
NAME, DATE, TIME, PHONE = range(4)

# --- Flask застосунок ---
app = Flask(__name__)

# --- Отримуємо токен з Render Secrets ---
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не знайдено у змінних середовища Render!")

# --- Функція для підключення до Google Calendar ---
def get_calendar_service():
    if not os.path.exists("token.json"):
        raise FileNotFoundError("❌ Файл token.json не знайдено! Завантаж його у Render як Secret File.")
    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/calendar.events"])
    service = build("calendar", "v3", credentials=creds)
    return service

# --- Перевірка вільного часу ---
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

# --- Початок розмови ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?")
    return NAME

# --- Ім’я ---
async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Дуже приємно! 🥰 На яку дату хочете записатись? (у форматі РРРР-ММ-ДД)")
    return DATE

# --- Дата ---
async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(update.message.text, "%Y-%m-%d").date()
        context.user_data["date"] = date
        await update.message.reply_text("⏰ Вкажіть бажаний час (у форматі ГГ:ХХ)")
        return TIME
    except ValueError:
        await update.message.reply_text("⚠️ Неправильний формат. Введіть у форматі: РРРР-ММ-ДД")
        return DATE

# --- Час ---
async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        context.user_data["time"] = time
        service = get_calendar_service()
        date = context.user_data["date"]

        if not is_time_slot_available(service, date, time):
            await update.message.reply_text("❌ На цей час уже є запис. Оберіть інший час.")
            return TIME

        await update.message.reply_text("📞 Вкажіть ваш номер телефону для підтвердження запису:")
        return PHONE

    except ValueError:
        await update.message.reply_text("⚠️ Неправильний формат часу. Введіть у форматі: ГГ:ХХ")
        return TIME

# --- Телефон ---
async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text
    context.user_data["phone"] = phone
    service = get_calendar_service()

    name = context.user_data["name"]
    date = context.user_data["date"]
    time = context.user_data["time"]
    start_time = datetime.datetime.combine(date, time)
    end_time = start_time + datetime.timedelta(minutes=90)

    event = {
        "summary": f"Запис: {name}",
        "description": f"Телефон: {phone}",
        "start": {"dateTime": start_time.isoformat(), "timeZone": "Europe/Kiev"},
        "end": {"dateTime": end_time.isoformat(), "timeZone": "Europe/Kiev"},
    }

    service.events().insert(calendarId="primary", body=event).execute()

    # --- "Візитка" підтвердження ---
    confirm_message = (
        "💅 *Запис підтверджено!*\n\n"
        f"👤 *Клієнт:* {name}\n"
        f"📅 *Дата:* {date.strftime('%d.%m.%Y')}\n"
        f"🕓 *Час:* {time.strftime('%H:%M')}\n"
        f"📞 *Телефон:* {phone}\n\n"
        "Дякуємо, що обрали салон краси *S3*! 💖"
    )

    await update.message.reply_text(confirm_message, parse_mode="Markdown")
    return ConversationHandler.END

# --- Скасування ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Запис скасовано. Якщо передумаєте — просто напишіть /start 💅")
    return ConversationHandler.END

# --- Основна логіка Telegram ---
application = Application.builder().token(TOKEN).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
        TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

application.add_handler(conv_handler)

# --- Обробка webhook ---
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    print("📩 Отримано оновлення:", update)
    application.update_queue.put_nowait(Update.de_json(update, application.bot))
    return "OK", 200

@app.route("/", methods=["GET"])
def home():
    return "💅 S3 Beauty Bot працює!", 200

# --- Запуск ---
if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=PORT)
