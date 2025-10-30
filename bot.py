import logging
import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler, filters, ContextTypes
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

# --- Конфігурація ---
TOKEN = "8302341867:AAFtCeDq2eEBWe7C857lfqTQ-IKOxskxZX4"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# --- Логи ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# --- Стан розмови ---
NAME, DATE, TIME, PHONE = range(4)

# --- Google Calendar ---
def get_calendar_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def is_time_slot_available(service, date, time):
    start_time = datetime.datetime.combine(date, time).isoformat() + "+02:00"
    end_time = (datetime.datetime.combine(date, time) + datetime.timedelta(minutes=90)).isoformat() + "+02:00"
    events_result = service.events().list(calendarId="primary", timeMin=start_time, timeMax=end_time,
                                          singleEvents=True, orderBy="startTime").execute()
    return len(events_result.get("items", [])) == 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Приємно познайомитися 🌸\nНа яку дату бажаєте записатись? (формат: 2025-11-05)")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(update.message.text, "%Y-%m-%d").date()
        context.user_data["date"] = date
        await update.message.reply_text("⏰ На котру годину бажаєте записатись? (наприклад: 14:30)")
        return TIME
    except ValueError:
        await update.message.reply_text("⚠️ Формат неправильний. Введіть дату у форматі YYYY-MM-DD.")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        context.user_data["time"] = time
        await update.message.reply_text("📞 Будь ласка, залиште свій номер телефону для підтвердження запису:")
        return PHONE
    except ValueError:
        await update.message.reply_text("⚠️ Формат часу неправильний. Введіть у форматі HH:MM.")
        return TIME

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text
    context.user_data["phone"] = phone
    name, date, time = context.user_data["name"], context.user_data["date"], context.user_data["time"]
    service = get_calendar_service()

    if not is_time_slot_available(service, date, time):
        await update.message.reply_text("⏰ На цей час уже є запис. Ось вільні варіанти:")
        free_slots = []
        for i in range(1, 6):
            new_time = (datetime.datetime.combine(date, time) + datetime.timedelta(minutes=90 * i)).time()
            if is_time_slot_available(service, date, new_time):
                free_slots.append(new_time.strftime("%H:%M"))
            if len(free_slots) >= 3: break
        await update.message.reply_text(", ".join(free_slots) if free_slots else "На жаль, вільних вікон немає 😔")
        return ConversationHandler.END

    start_time = datetime.datetime.combine(date, time).isoformat() + "+02:00"
    end_time = (datetime.datetime.combine(date, time) + datetime.timedelta(minutes=90)).isoformat() + "+02:00"
    event = {
        "summary": f"Запис у салоні краси S3 — {name}",
        "description": f"Телефон: {phone}",
        "start": {"dateTime": start_time, "timeZone": "Europe/Kiev"},
        "end": {"dateTime": end_time, "timeZone": "Europe/Kiev"},
    }
    service.events().insert(calendarId="primary", body=event).execute()

    confirmation = (
        "✨ *Ваш запис підтверджено!* ✨\n\n"
        f"👩‍💼 *Ім’я:* {name}\n"
        f"📅 *Дата:* {date.strftime('%d.%m.%Y')}\n"
        f"⏰ *Час:* {time.strftime('%H:%M')} — "
        f"{(datetime.datetime.combine(date, time) + datetime.timedelta(minutes=90)).time().strftime('%H:%M')}\n"
        f"📞 *Телефон:* {phone}\n\n"
        "💖 Чекаємо на вас у *S3 Beauty Salon!*\n"
        "_Естетика в кожній деталі._"
    )
    await update.message.reply_text(confirmation, parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Запис скасовано. Гарного дня 💫")
    return ConversationHandler.END

def main():
    app = ApplicationBuilder().token(TOKEN).build()
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
    app.add_handler(conv_handler)
    print("🤖 Бот запущений. Натисни /start у Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()
