import os
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    CallbackQueryHandler,
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import datetime

# ---------- CONFIG ----------
TOKEN = os.getenv("BOT_TOKEN", "8302341867:AAHd_faDWIBnC01wPdtoER75YaUb_gngdE0")
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_URL', 's3-beauty-bot.onrender.com')}/{TOKEN}"

# Google Calendar settings
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = "primary"  # або свій ID календаря
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

# ---------- LOGGING ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- FLASK ----------
app = Flask(__name__)
application = None


def get_calendar_service():
    """Підключення до Google Calendar."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)


# ---------- BOT HANDLERS ----------
async def start(update: Update, context: CallbackContext):
    """Привітання користувача."""
    keyboard = [
        [InlineKeyboardButton("💅 Записатися", callback_data="book")],
        [InlineKeyboardButton("ℹ️ Про нас", callback_data="info")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Вітаю 💖\nЯ — бот салону *S3 Beauty*!\nОбери дію нижче 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def button_handler(update: Update, context: CallbackContext):
    """Обробка натискань кнопок."""
    query = update.callback_query
    await query.answer()

    if query.data == "book":
        await query.message.reply_text("Напишіть, будь ласка, дату та час запису (наприклад: 5 листопада, 15:00)")
        context.user_data["booking_step"] = "waiting_for_datetime"

    elif query.data == "info":
        await query.message.reply_text("💅 Салон S3 Beauty — професійні послуги манікюру та педикюру у Києві 💖")


async def message_handler(update: Update, context: CallbackContext):
    """Обробка повідомлень користувача."""
    user_id = update.message.from_user.id
    text = update.message.text

    # Якщо користувач вводить дату/час після натискання "Записатися"
    if context.user_data.get("booking_step") == "waiting_for_datetime":
        context.user_data["datetime"] = text
        await update.message.reply_text(f"Ви хочете записатися на {text}? Підтвердити?",
                                        reply_markup=InlineKeyboardMarkup([
                                            [InlineKeyboardButton("✅ Так", callback_data="confirm_booking")],
                                            [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_booking")]
                                        ]))
        context.user_data["booking_step"] = "confirmation"

    else:
        await update.message.reply_text("Будь ласка, скористайтесь кнопками нижче /start")


async def confirm_booking(update: Update, context: CallbackContext):
    """Підтвердження запису."""
    query = update.callback_query
    await query.answer()

    datetime_text = context.user_data.get("datetime")
    if not datetime_text:
        await query.message.reply_text("Помилка: не знайдено дату та час запису.")
        return

    # Створення події у Google Calendar
    try:
        service = get_calendar_service()
        start_time = datetime.datetime.now() + datetime.timedelta(days=1)
        end_time = start_time + datetime.timedelta(hours=1)
        event = {
            'summary': f'Запис клієнта з Telegram',
            'description': f'Час: {datetime_text}',
            'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Europe/Kyiv'},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Europe/Kyiv'},
        }
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

        await query.message.reply_text("✅ Запис підтверджено! Дякуємо 💅")
        context.user_data.clear()

    except Exception as e:
        logger.error(f"Google Calendar error: {e}")
        await query.message.reply_text("⚠️ Сталася помилка при записі в календар.")


async def cancel_booking(update: Update, context: CallbackContext):
    """Скасування запису."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("❌ Запис скасовано.")
    context.user_data.clear()


# ---------- FLASK ROUTES ----------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    """Обробка вхідних оновлень від Telegram."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    return application.update_queue.put_nowait(update) or "ok"


@app.route("/", methods=["GET"])
def home():
    return "✅ S3 Beauty Telegram Bot is running!"


# ---------- MAIN APP ----------
def create_app():
    global application
    application = Application.builder().token(TOKEN).build()

    # handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^(book|info)$"))
    application.add_handler(CallbackQueryHandler(confirm_booking, pattern="confirm_booking"))
    application.add_handler(CallbackQueryHandler(cancel_booking, pattern="cancel_booking"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Set webhook
    async def set_webhook():
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"✅ Webhook set to: {WEBHOOK_URL}")

    application.run_polling = lambda: None  # disable polling
    application.initialize()
    application.post_init(set_webhook)
    return app, application


if __name__ == "__main__":
    app, telegram_app = create_app()
    app.run(host="0.0.0.0", port=PORT)
