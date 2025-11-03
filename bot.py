# bot.py — повний робочий код для S3 Beauty Salon bot (PTB 20.7 + Flask[async])
import os
import logging
import datetime
from zoneinfo import ZoneInfo
import asyncio
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

# Google API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN не знайдено у змінних середовища. Додай TOKEN (BotFather) у Render/ENV.")

PORT = int(os.environ.get("PORT", 10000))
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")  # Render встановлює цю змінну
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}/{TOKEN}" if RENDER_HOSTNAME else None

# Часовий пояс
TZ = ZoneInfo("Europe/Kyiv")

# Google Calendar scopes
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Google Calendar helpers ----------------
def get_calendar_service():
    """
    Повертає Google Calendar service.
    Потрібен token.json (отриманий через OAuth локально).
    Якщо token.json відсутній — підніме FileNotFoundError.
    """
    if not os.path.exists("token.json"):
        raise FileNotFoundError("token.json не знайдено. Згенеруйте локально та завантажте.")
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("calendar", "v3", credentials=creds)
    return service


def is_time_slot_available(service, start_dt: datetime.datetime, end_dt: datetime.datetime) -> bool:
    """
    Перевіряє, чи вільний слот між start_dt та end_dt (timezone-aware).
    Повертає True якщо вільно.
    """
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
    """
    Шукає до max_suggestions вільних слотів починаючи з desired_start,
    крок step_minutes, максимум lookahead_hours у майбутнє.
    Повертає список time-об'єктів.
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
        except HttpError as e:
            logger.error("Google API error when suggesting: %s", e)
            break
        current += datetime.timedelta(minutes=step_minutes)
    return suggestions


# ---------------- Conversation states ----------------
NAME, PHONE, DATE, TIME = range(4)

# ---------------- Flask app ----------------
app = Flask(__name__)

# ---------------- Telegram handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Початкове привітання — питаємо ім'я.
    """
    # Кнопка для відправки контакту при потребі
    contact_button = KeyboardButton("Надіслати контакт 📞", request_contact=True)
    kb = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?", reply_markup=kb)
    return NAME


async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data["name"] = name
    await update.message.reply_text("Приємно познайомитись! 😊\nНадішліть, будь ласка, ваш номер телефону (або натисніть кнопку для відправки контакту).")
    return PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Якщо користувач надіслав контакт — отримаємо з contact
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
    context.user_data["phone"] = phone
    await update.message.reply_text("На яку дату бажаєте записатись? Введіть у форматі РРРР-ММ-ДД (наприклад 2025-11-05):")
    return DATE


async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        date_obj = datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text("❌ Невірний формат дати. Введіть у форматі РРРР-ММ-ДД (наприклад 2025-11-05):")
        return DATE
    context.user_data["date"] = date_obj
    await update.message.reply_text("Вкажіть бажаний час (формат ГГ:ХХ, 24-годинний, наприклад 14:30):")
    return TIME


async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        t_obj = datetime.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await update.message.reply_text("❌ Невірний формат часу. Введіть у форматі ГГ:ХХ (наприклад 14:30):")
        return TIME

    date_obj = context.user_data.get("date")
    if not date_obj:
        await update.message.reply_text("❌ Помилка: дата не вказана. Почніть спочатку /start.")
        return ConversationHandler.END

    # timezone-aware start and end datetimes
    start_dt = datetime.datetime.combine(date_obj, t_obj).replace(tzinfo=TZ)
    end_dt = start_dt + datetime.timedelta(minutes=90)  # 1.5 години

    # Отримуємо Google Calendar сервіс
    try:
        service = get_calendar_service()
    except FileNotFoundError:
        await update.message.reply_text(
            "⚠️ На сервері не знайдено token.json (Google OAuth). "
            "Згенеруйте token.json локально і завантажте його у корінь проєкту."
        )
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Помилка авторизації Google: %s", e)
        await update.message.reply_text("⚠️ Помилка авторизації Google Calendar. Зверніться до адміністратора.")
        return ConversationHandler.END

    # Перевіряємо доступність
    try:
        if not is_time_slot_available(service, start_dt, end_dt):
            # Пропонуємо альтернативи
            suggestions = suggest_free_slots(service, start_dt)
            if suggestions:
                sug_text = ", ".join(s.strftime("%H:%M") for s in suggestions)
                await update.message.reply_text(f"⏰ На цей час уже є запис. Можу запропонувати: {sug_text}\nВведіть інший час або оберіть один із варіантів.")
            else:
                await update.message.reply_text("⏰ На найближчі години вільних слотів не знайдено. Спробуйте іншу дату або час.")
            return TIME
    except HttpError as e:
        logger.error("Google API HttpError при перевірці доступності: %s", e)
        await update.message.reply_text("⚠️ Помилка при перевірці календаря. Спробуйте пізніше.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Невідома помилка при перевірці доступності: %s", e)
        await update.message.reply_text("⚠️ Невідома помилка. Спробуйте пізніше.")
        return ConversationHandler.END

    # Якщо вільно — додаємо подію
    try:
        event_body = {
            "summary": f"S3 Beauty — запис: {context.user_data.get('name','Гість')}",
            "description": f"Ім'я: {context.user_data.get('name')}\nТелефон: {context.user_data.get('phone')}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Kyiv"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Kyiv"},
        }
        created = service.events().insert(calendarId="primary", body=event_body).execute()
    except HttpError as e:
        logger.error("Google API HttpError при створенні події: %s", e)
        await update.message.reply_text("⚠️ Не вдалося створити подію в календарі. Спробуйте пізніше.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Невідома помилка при створенні події: %s", e)
        await update.message.reply_text("⚠️ Невідома помилка при створенні запису.")
        return ConversationHandler.END

    # Надсилаємо підтвердження (текст + контакт-візитку)
    name = context.user_data.get("name", "Гість")
    phone = context.user_data.get("phone", "")
    try:
        await update.message.reply_text(
            "✅ Ваш запис підтверджено!\n\n"
            f"👩‍💼 Ім'я: {name}\n"
            f"📞 Телефон: {phone}\n"
            f"📅 Дата: {date_obj.strftime('%d.%m.%Y')}\n"
            f"⏰ Час: {start_dt.strftime('%H:%M')} — {end_dt.strftime('%H:%M')}\n\n"
            f"Посилання на подію: {created.get('htmlLink')}\n\n"
            "Дякуємо, до зустрічі у S3 Beauty Salon 💖"
        )
        # Надсилаємо контакт (як візитку)
        # Telegram метод send_contact в PTB: context.bot.send_contact(chat_id, phone_number, first_name)
        await context.bot.send_contact(chat_id=update.effective_chat.id, phone_number=phone, first_name=name)
    except Exception as e:
        logger.exception("Помилка надсилання підтвердження/контакту: %s", e)

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Запис скасовано. Якщо хочеш — почни знову /start")
    return ConversationHandler.END


# ---------------- Setup Application & ConversationHandler ----------------
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

# Optional simple commands
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Використай /start для запису. /cancel для відміни.")

application.add_handler(CommandHandler("help", help_cmd))


# ---------------- Flask webhook endpoints ----------------
@app.route(f"/{TOKEN}", methods=["POST"])
async def telegram_webhook():
    """
    Telegram шле POST сюди — перетворюємо на Update і передаємо в application.
    Flask[async] потрібен щоб async view працював.
    """
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.exception("Помилка обробки webhook: %s", e)
    return "ok", 200


@app.route("/", methods=["GET"])
def index():
    return "🤖 S3 Beauty Bot — running", 200


# ---------------- Run: встановлення webhook і запуск Flask ----------------
if __name__ == "__main__":
    async def _setup_and_run():
        # Встановлюємо webhook (якщо RENDER_HOSTNAME доступний)
        if WEBHOOK_URL:
            try:
                await application.bot.set_webhook(WEBHOOK_URL)
                logger.info("Webhook встановлено: %s", WEBHOOK_URL)
            except Exception as e:
                logger.exception("Не вдалося встановити webhook: %s", e)
        else:
            logger.warning("RENDER_EXTERNAL_HOSTNAME не знайдено — webhook не встановлено (локальний запуск).")

        # Запускаємо Flask (development server). На production можна використовувати gunicorn/uvicorn.
        app.run(host="0.0.0.0", port=PORT)

    asyncio.run(_setup_and_run())
