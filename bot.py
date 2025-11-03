# bot.py — повний робочий бот для S3 Beauty Salon
import os
import asyncio
import logging
import datetime
from zoneinfo import ZoneInfo
from typing import List

from flask import Flask, request
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Google
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------- CONFIG ----------------
# Рекомендується задати TOKEN як змінну оточення на Render.
# Якщо не заданий, використовуємо значення тут (тільки для швидкого тесту).
TOKEN = os.getenv("TOKEN", "8302341867:AAHd_faDWIBnC01wPdtoER75YaUb_gngdE0")
if not TOKEN:
    raise RuntimeError("TOKEN не знайдено. Додайте змінну оточення TOKEN.")

PORT = int(os.getenv("PORT", 10000))
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")  # Render встановлює це автоматично
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}/{TOKEN}" if RENDER_HOSTNAME else None

# Timezone
TZ = ZoneInfo("Europe/Kyiv")

# Google scopes (Calendar + Sheets)
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/spreadsheets"
]

# Spreadsheet ID (якщо хочеш записувати у Google Sheets)
# Можеш додати у змінні оточення або вставити сюди.
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")  # наприклад: "1AbCdEfGhI..."

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Google helpers ----------------
def get_google_credentials():
    """
    Читає token.json з кореню проєкту.
    Повертає Credentials або викидає FileNotFoundError.
    """
    if not os.path.exists("token.json"):
        raise FileNotFoundError("token.json не знайдено. Згенеруйте локально і завантажте.")
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return creds

def get_calendar_service():
    creds = get_google_credentials()
    return build("calendar", "v3", credentials=creds)

def get_sheets_service():
    creds = get_google_credentials()
    return build("sheets", "v4", credentials=creds)

def is_time_slot_available(service, start_dt: datetime.datetime, end_dt: datetime.datetime) -> bool:
    time_min = start_dt.isoformat()
    time_max = end_dt.isoformat()
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=1,
        )
        .execute()
    )
    items = events_result.get("items", [])
    return len(items) == 0

def suggest_free_slots(
    service,
    desired_start: datetime.datetime,
    duration_minutes: int = 90,
    max_suggestions: int = 3,
    step_minutes: int = 30,
    lookahead_hours: int = 8,
) -> List[datetime.time]:
    suggestions = []
    current = desired_start
    end_limit = desired_start + datetime.timedelta(hours=lookahead_hours)
    while current < end_limit and len(suggestions) < max_suggestions:
        start = current
        end = start + datetime.timedelta(minutes=duration_minutes)
        try:
            if is_time_slot_available(service, start, end):
                suggestions.append(start.time())
        except HttpError as e:
            logger.error("Google API error when suggesting: %s", e)
            break
        current += datetime.timedelta(minutes=step_minutes)
    return suggestions

def create_calendar_event(service, start_dt: datetime.datetime, end_dt: datetime.datetime, title: str, description: str):
    event_body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Kyiv"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Kyiv"},
    }
    created = service.events().insert(calendarId="primary", body=event_body).execute()
    return created

def write_to_sheet(service, spreadsheet_id: str, row: List[str]):
    if not spreadsheet_id:
        logger.info("SPREADSHEET_ID пустий — пропускаємо запис у Sheets.")
        return
    range_name = "Записи!A1"  # припущення: аркуш "Записи"
    body = {"values": [row]}
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=range_name, valueInputOption="RAW", body=body
    ).execute()

# ---------------- Conversation states ----------------
NAME, PHONE, DATE, TIME = range(4)

# ---------------- Flask app ----------------
app = Flask(__name__)

# ---------------- Telegram handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact_button = KeyboardButton("Надіслати контакт 📞", request_contact=True)
    kb = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?", reply_markup=kb)
    return NAME

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Приємно познайомитись! 😊\nНадішліть, будь ласка, ваш номер телефону або натисніть кнопку для відправки контакту.")
    return PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
    context.user_data["phone"] = phone
    await update.message.reply_text("На яку дату бажаєтесь записатись? Введіть у форматі РРРР-ММ-ДД (наприклад 2025-11-05):")
    return DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        date_obj = datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text("❌ Невірний формат дати. Введіть у форматі РРРР-ММ-ДД:")
        return DATE
    context.user_data["date"] = date_obj
    await update.message.reply_text("Вкажіть бажаний час (формат ГГ:ХХ, 24-годинний, наприклад 14:30):")
    return TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        t_obj = datetime.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await update.message.reply_text("❌ Невірний формат часу. Спробуйте ще раз (ГГ:ХХ):")
        return TIME

    date_obj = context.user_data.get("date")
    if not date_obj:
        await update.message.reply_text("❌ Помилка: дата не задана. Спробуйте /start.")
        return ConversationHandler.END

    start_dt = datetime.datetime.combine(date_obj, t_obj).replace(tzinfo=TZ)
    end_dt = start_dt + datetime.timedelta(minutes=90)

    # Google service
    try:
        cal_service = get_calendar_service()
    except FileNotFoundError:
        await update.message.reply_text("⚠️ token.json не знайдено на сервері. Завантажте token.json у корінь проєкту.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Google auth error: %s", e)
        await update.message.reply_text("⚠️ Помилка авторизації Google. Зверніться до адміністратора.")
        return ConversationHandler.END

    # Check availability
    try:
        if not is_time_slot_available(cal_service, start_dt, end_dt):
            suggestions = suggest_free_slots(cal_service, start_dt)
            if suggestions:
                sug_text = ", ".join(s.strftime("%H:%M") for s in suggestions)
                await update.message.reply_text(f"⏰ На цей час уже є запис. Можу запропонувати: {sug_text}\nВведіть інший час або оберіть один із варіантів.")
            else:
                await update.message.reply_text("⏰ Вільних слотів поруч не знайдено. Спробуйте іншу дату або час.")
            return TIME
    except HttpError as e:
        logger.error("Google HttpError при перевірці: %s", e)
        await update.message.reply_text("⚠️ Помилка при зверненні до Google Calendar. Спробуйте пізніше.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Error checking availability: %s", e)
        await update.message.reply_text("⚠️ Невідома помилка.")
        return ConversationHandler.END

    # Create event
    try:
        title = f"S3 Beauty — запис: {context.user_data.get('name','Гість')}"
        description = f"Ім'я: {context.user_data.get('name')}\nТелефон: {context.user_data.get('phone')}"
        created = create_calendar_event(cal_service, start_dt, end_dt, title, description)
    except Exception as e:
        logger.exception("Error creating event: %s", e)
        await update.message.reply_text("⚠️ Не вдалося створити подію в календарі.")
        return ConversationHandler.END

    # Write to Sheets (optional)
    try:
        sheets_service = get_sheets_service()
        row = [
            context.user_data.get("name", ""),
            context.user_data.get("phone", ""),
            date_obj.strftime("%Y-%m-%d"),
            t_obj.strftime("%H:%M"),
            created.get("htmlLink", ""),
        ]
        write_to_sheet(sheets_service, SPREADSHEET_ID, row)
    except FileNotFoundError:
        logger.info("token.json відсутній — пропускаємо запис у Sheets.")
    except Exception as e:
        logger.exception("Error writing to Sheets: %s", e)

    # Send confirmation + contact
    try:
        await update.message.reply_text(
            "✅ Запис підтверджено!\n\n"
            f"👩‍💼 Ім'я: {context.user_data.get('name')}\n"
            f"📞 Телефон: {context.user_data.get('phone')}\n"
            f"📅 Дата: {date_obj.strftime('%d.%m.%Y')}\n"
            f"⏰ Час: {start_dt.strftime('%H:%M')} — {end_dt.strftime('%H:%M')}\n\n"
            f"Посилання на подію: {created.get('htmlLink')}\n\n"
            "Дякуємо, до зустрічі у S3 Beauty Salon 💖"
        )
        # send contact card
        await context.bot.send_contact(chat_id=update.effective_chat.id,
                                       phone_number=context.user_data.get("phone", ""),
                                       first_name=context.user_data.get("name", ""))
    except Exception as e:
        logger.exception("Error sending confirmation: %s", e)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано. Якщо потрібно — почни знову /start")
    return ConversationHandler.END

# ---------------- Setup Application ----------------
application = Application.builder().token(TOKEN).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
        PHONE: [
            MessageHandler(filters.CONTACT, handle_phone),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone),
        ],
        DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date)],
        TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True,
)

application.add_handler(conv_handler)

# Simple help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Використай /start щоб записатись. /cancel щоб скасувати.")

application.add_handler(CommandHandler("help", help_cmd))

# ---------------- Flask webhook endpoints ----------------
@app.route(f"/{TOKEN}", methods=["POST"])
async def telegram_webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.exception("Webhook processing error: %s", e)
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "🤖 S3 Beauty Bot — running", 200

# ---------------- Run ----------------
if __name__ == "__main__":
    async def _setup_and_run():
        if WEBHOOK_URL:
            try:
                await application.bot.set_webhook(WEBHOOK_URL)
                logger.info("Webhook set to %s", WEBHOOK_URL)
            except Exception as e:
                logger.exception("Failed to set webhook: %s", e)
        else:
            logger.warning("RENDER_EXTERNAL_HOSTNAME not found — webhook not set.")

        app.run(host="0.0.0.0", port=PORT)

    asyncio.run(_setup_and_run())
