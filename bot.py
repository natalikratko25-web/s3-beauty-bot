import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------------- ЛОГІ ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- НАЛАШТУВАННЯ ----------------
BOT_TOKEN = "8302341867:AAHd_faDWIBnC01wPdtoER75YaUb_gngdE0"
WEBHOOK_URL = "https://s3-beauty-bot.onrender.com"

SCOPES = ['https://www.googleapis.com/auth/calendar']
creds = None

# ---------------- GOOGLE AUTH ----------------
if os.path.exists('token.json'):
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
else:
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    creds = flow.run_local_server(port=0)
    with open('token.json', 'w') as token:
        token.write(creds.to_json())

service = build('calendar', 'v3', credentials=creds)

# ---------------- FLASK APP ----------------
app = Flask(__name__)

# ---------------- ДОПОМОЖНІ ФУНКЦІЇ ----------------
def send_message(chat_id, text, reply_markup=None):
    """Надсилання повідомлення користувачу"""
    from telegram import Bot
    bot = Bot(BOT_TOKEN)
    bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")

# ---------------- ОБРОБНИКИ ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💅 Записатися", callback_data="book")],
    ]
    await update.message.reply_text(
        "Привіт 👋\n"
        "Я бот *S3 Beauty Salon* 💖\n"
        "Допоможу записатися на процедуру 🪄",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введіть ваше ім’я 👩‍💼:")
    context.user_data.clear()
    context.user_data['step'] = 'name'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id
    step = context.user_data.get('step')

    if step == 'name':
        context.user_data['name'] = text
        context.user_data['step'] = 'phone'
        await update.message.reply_text("Введіть ваш номер телефону 📞:")
    elif step == 'phone':
        context.user_data['phone'] = text
        context.user_data['step'] = 'date'
        await update.message.reply_text("Вкажіть дату (у форматі *ДД.ММ.РРРР*):", parse_mode="Markdown")
    elif step == 'date':
        try:
            date = datetime.strptime(text, "%d.%m.%Y").date()
            context.user_data['date'] = date
            context.user_data['step'] = 'time'
            await update.message.reply_text("Вкажіть час (у форматі *ГГ:ХХ*):", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("Неправильний формат дати. Спробуйте ще раз 📅")
    elif step == 'time':
        try:
            time = datetime.strptime(text, "%H:%M").time()
            date = context.user_data['date']
            dt = datetime.combine(date, time)
            context.user_data['time'] = time
            context.user_data['datetime'] = dt

            keyboard = [
                [InlineKeyboardButton("✅ Підтвердити", callback_data="confirm")],
                [InlineKeyboardButton("❌ Скасувати", callback_data="cancel")],
            ]
            await update.message.reply_text(
                f"Підтвердіть ваш запис:\n\n"
                f"👩‍💼 Ім’я: {context.user_data['name']}\n"
                f"📞 Телефон: {context.user_data['phone']}\n"
                f"📅 Дата: {date.strftime('%d.%m.%Y')}\n"
                f"⏰ Час: {time.strftime('%H:%M')}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data['step'] = 'confirm'
        except ValueError:
            await update.message.reply_text("Невірний формат часу. Спробуйте ще раз ⏰")

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data

    if query.data == "confirm":
        # --- Додаємо подію в Google Calendar ---
        event = {
            'summary': f"Запис: {data['name']}",
            'description': f"Телефон: {data['phone']}",
            'start': {
                'dateTime': data['datetime'].isoformat(),
                'timeZone': 'Europe/Kiev',
            },
            'end': {
                'dateTime': (data['datetime'] + timedelta(hours=1)).isoformat(),
                'timeZone': 'Europe/Kiev',
            },
        }
        service.events().insert(calendarId='primary', body=event).execute()

        # --- Підтвердження користувачу ---
        await query.edit_message_text(
            "✨ *Запис підтверджено!*\n\n"
            f"👩‍💼 Ім’я: {data['name']}\n"
            f"📞 Телефон: {data['phone']}\n"
            f"📅 Дата: {data['date'].strftime('%d.%m.%Y')}\n"
            f"⏰ Час: {data['time'].strftime('%H:%M')} - "
            f"{(data['datetime'] + timedelta(hours=1)).strftime('%H:%M')}\n\n"
            "💅 Дякуємо, що обрали *S3 Beauty Salon*!\n"
            "Чекаємо на вас у призначений час 💖",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❌ Запис скасовано.")

# ---------------- СТВОРЕННЯ ТЕЛЕГРАМ ДОДАТКУ ----------------
def create_app():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_booking, pattern="^book$"))
    application.add_handler(CallbackQueryHandler(handle_confirmation, pattern="^(confirm|cancel)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app, application

# ---------------- WEBHOOK ----------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), Application.builder().token(BOT_TOKEN).build().bot)
    app.telegram_app.update_queue.put_nowait(update)
    return "ok", 200

# ---------------- ГОЛОВНИЙ ВХІД ----------------
if __name__ == "__main__":
    app, telegram_app = create_app()
    app.telegram_app = telegram_app
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
    )
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
