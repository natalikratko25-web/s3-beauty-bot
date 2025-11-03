import os
import logging
import datetime
import asyncio
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ====== CONFIG ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не задано у Render Environment variables")

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "s3-beauty-bot.onrender.com")
WEBHOOK_URL = f"https://{RENDER_URL}/{BOT_TOKEN}"

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
CALENDAR_ID = "primary"

# ====== LOGGING ======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== FLASK APP ======
app = Flask(__name__)

# ====== GOOGLE CALENDAR ======
def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        if not os.path.exists(CREDENTIALS_FILE):
            raise RuntimeError("credentials.json не знайдено! Спочатку створи token.json локально.")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

# ====== TELEGRAM HANDLERS ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💅 Записатися", callback_data="book")],
        [InlineKeyboardButton("ℹ️ Про нас", callback_data="info")],
    ]
    await update.message.reply_text(
        "Вітаю 💅 Я бот салону краси S3!\nОберіть дію нижче:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "book":
        context.user_data["flow"] = "name"
        await q.message.reply_text("Як вас звати?")
    elif q.data == "info":
        await q.message.reply_text("💖 Салон краси S3 — естетика у кожній деталі!")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    flow = context.user_data.get("flow")

    if flow == "name":
        context.user_data["name"] = text
        context.user_data["flow"] = "phone"
        await update.message.reply_text("Вкажіть, будь ласка, номер телефону:")
        return

    if flow == "phone":
        context.user_data["phone"] = text
        context.user_data["flow"] = "date"
        await update.message.reply_text("На яку дату бажаєте записатись? (формат YYYY-MM-DD)")
        return

    if flow == "date":
        try:
            d = datetime.datetime.strptime(text, "%Y-%m-%d").date()
            context.user_data["date"] = d
            context.user_data["flow"] = "time"
            await update.message.reply_text("О котрій годині? (формат HH:MM)")
        except ValueError:
            await update.message.reply_text("Невірний формат дати. Використайте YYYY-MM-DD.")
        return

    if flow == "time":
        try:
            t = datetime.datetime.strptime(text, "%H:%M").time()
            context.user_data["time"] = t

            # Google Calendar
            try:
                service = get_calendar_service()
            except Exception as e:
                logger.error("Google auth error: %s", e)
                await update.message.reply_text("⚠️ Помилка авторизації Google. Спочатку створи token.json локально.")
                return

            start_dt = datetime.datetime.combine(context.user_data["date"], context.user_data["time"])
            end_dt = start_dt + datetime.timedelta(minutes=90)

            events = service.events().list(
                calendarId=CALENDAR_ID,
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True,
                orderBy="startTime"
            ).execute()

            if events.get("items"):
                await update.message.reply_text("❌ Цей час уже зайнятий. Оберіть інший.")
                return

            event = {
                "summary": f"S3 Запис — {context.user_data.get('name')}",
                "description": f"Телефон: {context.user_data.get('phone')}",
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Kyiv"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Kyiv"},
            }
            service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

            await update.message.reply_text(
                f"✅ Запис підтверджено!\nІм’я: {context.user_data.get('name')}\n"
                f"Телефон: {context.user_data.get('phone')}\n"
                f"Дата: {start_dt.strftime('%d.%m.%Y')}, час: {start_dt.strftime('%H:%M')}"
            )
            context.user_data.clear()

        except ValueError:
            await update.message.reply_text("Невірний формат часу. Використайте HH:MM.")
        return

    await update.message.reply_text("Використайте команду /start, щоб почати.")

# ====== TELEGRAM APP ======
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(handle_buttons))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ====== FLASK ROUTES ======
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.create_task(application.process_update(update))
    return "OK"

@app.route("/", methods=["GET"])
def index():
    return "✅ S3 Beauty Bot працює!"

# ====== MAIN ENTRY ======
if __name__ == "__main__":
    async def main():
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to: {WEBHOOK_URL}")
        app.run(host="0.0.0.0", port=PORT)

    asyncio.run(main())
