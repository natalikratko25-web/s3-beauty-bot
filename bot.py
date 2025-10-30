import logging
import datetime
import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# === Google Calendar ===
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def get_calendar_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        # Якщо ще не авторизовано — запускаємо браузер
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def is_time_slot_available(service, date, time):
    start_datetime = datetime.datetime.combine(date, time)
    end_datetime = start_datetime + datetime.timedelta(minutes=90)
    events_result = service.events().list(
        calendarId="primary",
        timeMin=start_datetime.isoformat(),
        timeMax=end_datetime.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

def main():
    TOKEN = "8302341867:AAFtCeDq2eEBWe7C857lfqTQ-IKOxskxZX4"  # <-- ось тут вставляєш свій токен
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущений. Натисни /start у Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()

    return not events_result.get("items", [])


# === Стан розмови ===
ASK_NAME, ASK_DATE, ASK_TIME, ASK_PHONE = range(4)

# === Обробники ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💅 Вітаю! Давайте знайомитися. Я бот салону краси *S3 Beauty Salon*!\nА як вас звати?", parse_mode="Markdown")
    return ASK_NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("🗓 Вкажіть, будь ласка, дату запису (у форматі ДД.ММ.РРРР):")
    return ASK_DATE


async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(update.message.text, "%d.%m.%Y").date()
        context.user_data["date"] = date
        await update.message.reply_text("⏰ Чудово! А на котру годину бажаєте запис?")
        return ASK_TIME
    except ValueError:
        await update.message.reply_text("⚠️ Неправильний формат дати. Спробуйте ще раз (ДД.ММ.РРРР):")
        return ASK_DATE


async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        context.user_data["time"] = time
        await update.message.reply_text("📞 І ще ваш номер телефону, будь ласка:")
        return ASK_PHONE
    except ValueError:
        await update.message.reply_text("⚠️ Невірний формат часу. Спробуйте ще раз (ГГ:ХХ):")
        return ASK_TIME


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text
    context.user_data["phone"] = phone
    name, date, time = context.user_data["name"], context.user_data["date"], context.user_data["time"]

    await update.message.rep
    


