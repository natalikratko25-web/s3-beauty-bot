import os
import logging
import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")  # обов'язково додай TOKEN в Render env vars
PORT = int(os.environ.get("PORT", 10000))
RENDER_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")  # Render автоматично задає
TIMEZONE = ZoneInfo("Europe/Kyiv")  # використовуємо часовий пояс Kyiv
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

if not TOKEN:
    raise RuntimeError("TOKEN не знайдено у змінних середовища. Додай змінну TOKEN у Render.")

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Google Calendar helpers ----------------
def get_calendar_service():
    """
    Повертає об'єкт service для Google Calendar.
    Потрібен файл token.json (створюється локально через get_token.py).
    """
    if not os.path.exists("token.json"):
        raise FileNotFoundError("token.json не знайдено. Створи його локально за допомогою get_token.py.")
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    return build("calendar", "v3", credentials=creds)


def is_time_slot_available(service, start_dt: datetime.datetime, end_dt: datetime.datetime) -> bool:
    """
    Перевіряє чи є в календарі події в інтервалі [start_dt, end_dt).
    start_dt та end_dt повинні бути timezone-aware.
    Повертає True якщо вільно.
    """
    try:
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
    except HttpError as e:
        logger.error(f"Google API HttpError: {e}")
        raise


def suggest_free_slots(service, desired_start: datetime.datetime, duration_minutes=90, max_suggestions=3, step_minutes=30, lookahead_hours=8):
    """
    Пропонує кілька найближчих вільних слотів (за кроком step_minutes) у межах lookahead_hours.
    Повертає список datetime.time у локальному часі.
    """
    suggestions = []
    current = desired_start
    end_limit = desired_start + datetime.timedelta(hours=lookahead_hours)
    while current < end_limit and len(suggestions) < max_suggestions:
        start = current
        end = start + datetime.timedelta(minutes=duration_minutes)
        try:
            if is_time_slot_available(service, start, end):
                suggestions.append(start.time())
        except Exception:
            break
        current += datetime.timedelta(minutes=step_minutes)
    return suggestions

# ---------------- Conversation states ----------------
NAME, PHONE, DATE, TIME = range(4)

# ---------------- TELEGRAM handlers ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Дякую 🎀 Введіть, будь ласка, ваш номер телефону (наприклад +380981234567):")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    await update.message.reply_text("На яку дату бажаєте записатись? Введіть у форматі РРРР-ММ-ДД (наприклад 2025-11-05):")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        date = datetime.datetime.strptime(text, "%Y-%m-%d").date()
        context.user_data["date"] = date
        await update.message.reply_text("Оберіть час (формат ГГ:ХХ, 24-год):")
        return TIME
    except ValueError:
        await update.message.reply_text("Невірний формат дати. Спробуйте ще: РРРР-ММ-ДД")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        t = datetime.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await update.message.reply_text("Невірний формат часу. Введіть у форматі ГГ:ХХ (наприклад 14:30).")
        return TIME

    # Формуємо timezone-aware datetime
    date = context.user_data["date"]
    start_dt = datetime.datetime.combine(date, t).replace(tzinfo=TIMEZONE)
    end_dt = start_dt + datetime.timedelta(minutes=90)  # 1.5 години

    # Отримуємо сервіс календаря
    try:
        service = get_calendar_service()
    except FileNotFoundError:
        await update.message.reply_text(
            "⚠️ Файл token.json не знайдено на сервері. Спочатку згенеруйте token.json локально (get_token.py) "
            "і завантажте його у директорію проєкту."
        )
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Помилка Google auth")
        await update.message.reply_text("⚠️ Помилка авторизації Google Calendar. Перевірте token.json/credentials.json.")
        return ConversationHandler.END

    # Перевірка доступності слота
    try:
        if not is_time_slot_available(service, start_dt, end_dt):
            # запропонувати альтернативні слоти
            suggestions = suggest_free_slots(service, start_dt)
            if suggestions:
                sug_text = ", ".join([s.strftime("%H:%M") for s in suggestions])
                await update.message.reply_text(f"⏰ На цей час уже є запис. Можу запропонувати: {sug_text}\nВведіть інший час або оберіть один із варіантів.")
            else:
                await update.message.reply_text("⏰ На найближчі години вільних слотів не знайдено. Введіть інший час або іншу дату.")
            return TIME
    except HttpError:
        await update.message.reply_text("⚠️ Помилка при перевірці календаря. Спробуйте пізніше.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Error checking availability")
        await update.message.reply_text("⚠️ Невідома помилка при перевірці. Спробуйте пізніше.")
        return ConversationHandler.END

    # Якщо вільно — створюємо подію
    try:
        event = {
            "summary": f"Запис S3 — {context.user_data['name']}",
            "description": f"Телефон: {context.user_data['phone']}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Kyiv"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Kyiv"},
        }
        created = service.events().insert(calendarId="primary", body=event).execute()
    except HttpError as e:
        logger.error(f"Google API error on insert: {e}")
        await update.message.reply_text("⚠️ Не вдалося створити подію у календарі. Спробуйте пізніше.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Error creating event")
        await update.message.reply_text("⚠️ Невідома помилка при створенні події.")
        return ConversationHandler.END

    # Надсилаємо підтвердження (візитка)
    await update.message.reply_text(
        "✅ Ваш запис підтверджено!\n\n"
        f"👩‍💼 Ім'я: {context.user_data['name']}\n"
        f"📞 Телефон: {context.user_data['phone']}\n"
        f"📅 Дата: {date.strftime('%d.%m.%Y')}\n"
        f"⏰ Час: {start_dt.strftime('%H:%M')} — {end_dt.strftime('%H:%M')}\n\n"
        f"Посилання на подію: {created.get('htmlLink')}\n\n"
        "До зустрічі у S3 Beauty Salon 💖"
    )

    # Тут можна додатково надсилати візитку vCard — приклад нижче (необов'язково)
    # (Telegram-Contact / vCard можна відправити іншим методом, якщо потрібно)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано ❌")
    return ConversationHandler.END

# ---------------- Flask + Telegram setup ----------------
app = Flask(__name__)

# Створюємо Application (без Updater)
application = Application.builder().token(TOKEN).build()

# ConversationHandler для запису
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_cmd)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
        TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True,
)

# Інші корисні команди
application.add_handler(conv_handler)
application.add_handler(CommandHandler("help", lambda u, c: c.bot.send_message(u.effective_chat.id, "Використай /start для запису або /cancel для відміни.")))

# Прив'язуємо об'єкти до Flask, щоб доступні були у webhook
app.bot = application.bot
app.application = application

# Webhook endpoint (Telegram робить POST сюди)
@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, app.bot)
    # Обробляємо оновлення через application
    await app.application.process_update(update)
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "🤖 S3 Beauty Bot — running", 200

# ---------------- Run: встановлюємо webhook та запускаємо Flask ----------------
if __name__ == "__main__":
    import asyncio
    async def setup_and_run():
        # Встановлюємо webhook (Render надає RENDER_EXTERNAL_HOSTNAME)
        hostname = RENDER_HOSTNAME or os.environ.get("HOSTNAME") or None
        if hostname:
            webhook_url = f"https://{hostname}/{TOKEN}"
            await application.bot.set_webhook(webhook_url)
            logger.info(f"Webhook встановлено: {webhook_url}")
        else:
            logger.warning("RENDER_EXTERNAL_HOSTNAME не знайдено — не встановлено webhook (локальний запуск).")

        # Запускаємо Flask (синхронно) — Flask[async] дозволяє async views
        app.run(host="0.0.0.0", port=PORT)

    asyncio.run(setup_and_run())
