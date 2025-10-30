import os
import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- Google Calendar setup ---
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def get_calendar_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)

# --- Conversation states ---
NAME, DATE, TIME, PHONE = range(4)

# --- Start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?")
    return NAME

# --- Get name ---
async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("Приємно познайомитись 💖! На яку дату бажаєте записатися? (у форматі РРРР-ММ-ДД)")
    return DATE

# --- Get date ---
async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['date'] = update.message.text
    await update.message.reply_text("⏰ На котру годину бажаєте? (наприклад 14:30)")
    return TIME

# --- Check available time ---
def is_time_slot_available(service, date, time):
    start_time = datetime.datetime.combine(date, time)
    end_time = start_time + datetime.timedelta(minutes=90)
    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.isoformat(),
        timeMax=end_time.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    return len(events_result.get('items', [])) == 0

# --- Get time ---
async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(context.user_data['date'], "%Y-%m-%d").date()
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
    except ValueError:
        await update.message.reply_text("⚠️ Формат невірний. Введіть час у форматі HH:MM, наприклад 15:00")
        return TIME

    service = get_calendar_service()

    if not is_time_slot_available(service, date, time):
        await update.message.reply_text("❌ Цей час зайнятий. Виберіть, будь ласка, інший час.")
        return TIME

    context.user_data['time'] = update.message.text
    await update.message.reply_text("📞 Будь ласка, залиште ваш номер телефону для підтвердження запису.")
    return PHONE

# --- Get phone and confirm ---
async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text
    context.user_data['phone'] = phone

    name = context.user_data['name']
    date = context.user_data['date']
    time = context.user_data['time']

    # --- Add event to Google Calendar ---
    service = get_calendar_service()
    start_time = datetime.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    end_time = start_time + datetime.timedelta(minutes=90)

    event = {
        'summary': f'Запис клієнта {name}',
        'description': f'Телефон: {phone}',
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Europe/Kiev'},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Europe/Kiev'},
    }

    service.events().insert(calendarId='primary', body=event).execute()

    await update.message.reply_text(
        f"💅 Запис підтверджено!\n\n🧾 Ім'я: {name}\n📅 Дата: {date}\n⏰ Час: {time}\n📞 Телефон: {phone}\n\nДо зустрічі у салоні S3 ❤️"
    )

    return ConversationHandler.END

# --- Cancel handler ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Запис скасовано.")
    return ConversationHandler.END

# --- Main ---
def main():
    TOKEN = os.getenv("BOT_TOKEN")  # <-- Береться зі змінної середовища Render
    if not TOKEN:
        print("❌ ПОМИЛКА: BOT_TOKEN не знайдено. Додай його у Render → Environment → BOT_TOKEN")
        return

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
    print("🤖 Бот запущено на Render! Натисни /start у Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()
