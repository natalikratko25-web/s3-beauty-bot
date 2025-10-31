import os
import datetime
import threading
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# === Flask "фіктивний" сервер для Render ===
app = Flask(__name__)

@app.route('/')
def home():
    return "S3 Beauty Salon 💅 Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)

# === СТАНИ для розмови ===
NAME, PHONE, DATE, TIME = range(4)

# === Отримання Google Calendar сервісу ===
def get_calendar_service():
    if not os.path.exists("token.json"):
        raise FileNotFoundError("❌ Файл token.json не знайдено. Завантажте його на Render (в Files або як Secret File).")

    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/calendar.events"])
    return build("calendar", "v3", credentials=creds)

# === Перевірка вільного часу ===
def is_time_slot_available(service, date, time):
    start_time = datetime.datetime.combine(date, time)
    end_time = start_time + datetime.timedelta(minutes=90)

    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.isoformat() + "Z",
        timeMax=end_time.isoformat() + "Z",
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    return not events_result.get("items", [])

# === Команди ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Приємно познайомитися 🌸 А який у вас номер телефону?")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("Вкажіть дату, будь ласка (у форматі РРРР-ММ-ДД):")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        date = datetime.datetime.strptime(update.message.text, "%Y-%m-%d").date()
        context.user_data["date"] = date
        await update.message.reply_text("Чудово! 🕐 А на яку годину бажаєте запис?")
        return TIME
    except ValueError:
        await update.message.reply_text("⚠️ Будь ласка, введіть дату у форматі РРРР-ММ-ДД.")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time = datetime.datetime.strptime(update.message.text, "%H:%M").time()
        date = context.user_data["date"]
        context.user_data["time"] = time

        service = get_calendar_service()

        if not is_time_slot_available(service, date, time):
            await update.message.reply_text("⏰ Цей час уже зайнятий. Ось кілька вільних варіантів:")

            free_slots = []
            for i in range(1, 6):
                new_time = (datetime.datetime.combine(date, time) + datetime.timedelta(minutes=90 * i)).time()
                if is_time_slot_available(service, date, new_time):
                    free_slots.append(new_time.strftime("%H:%M"))
                if len(free_slots) >= 3:
                    break

            await update.message.reply_text(", ".join(free_slots) if free_slots else "Немає вільних слотів 😔")
            return TIME

        # Створюємо подію
        start_time = datetime.datetime.combine(date, time)
        end_time = start_time + datetime.timedelta(minutes=90)

        event = {
            'summary': f'Запис: {context.user_data["name"]}',
            'description': f'Телефон: {context.user_data["phone"]}',
            'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Europe/Kiev'},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Europe/Kiev'}
        }

        service.events().insert(calendarId='primary', body=event).execute()

        confirmation = (
            f"✨ *Ваш запис підтверджено!*\n\n"
            f"👩 Ім’я: {context.user_data['name']}\n"
            f"📞 Телефон: {context.user_data['phone']}\n"
            f"📅 Дата: {date.strftime('%d.%m.%Y')}\n"
            f"🕒 Час: {time.strftime('%H:%M')} – {(end_time).strftime('%H:%M')}\n\n"
            f"Дякуємо, що обираєте S3 Beauty Salon 💖"
        )

        await update.message.reply_text(confirmation, parse_mode="Markdown")
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("⚠️ Будь ласка, введіть час у форматі ГГ:ХХ.")
        return TIME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Запис скасовано. Гарного дня 💅")
    return ConversationHandler.END

# === Запуск ===
def main():
    TOKEN = os.getenv("BOT_TOKEN")  # ✅ Токен задається через Render Secret
    if not TOKEN:
        raise ValueError("❌ BOT_TOKEN не знайдено. Додай його в Render → Environment → Secret.")

    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    print("🤖 Бот запущено. Очікуємо повідомлення... 💅")
    app.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    main()
