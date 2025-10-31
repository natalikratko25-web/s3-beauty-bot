# bot.py
import os
import logging
import datetime
from zoneinfo import ZoneInfo
from typing import Optional, List

from flask import Flask, request
from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# Google API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ----------------- НАЛАШТУВАННЯ -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота — обов'язково зберігай як Environment variable на Render / локально
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    logger.error("BOT_TOKEN не знайдено у змінних середовища. Встанови BOT_TOKEN.")
    raise SystemExit("BOT_TOKEN not set")

# Часова зона салону
TZ = ZoneInfo("Europe/Kyiv")

# Google Calendar scope
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Шлях до credentials.json (OAuth client) та token.json (отриманий токен)
CREDENTIALS_FILE = "credentials.json"  # повинен бути завантажений в корінь репо
TOKEN_FILE = "token.json"  # збережений refresh/access token після авторизації

# Тривалість запису в хвилинах (1.5 години = 90 хв)
SERVICE_DURATION_MIN = 90

# Скільки альтернатив запропонувати, якщо слот зайнятий
ALTERNATIVE_SLOTS_TO_SHOW = 3

# ----------------- GOOGLE CALENDAR -----------------
def get_calendar_service():
    """
    Повертає авторизований сервіс Google Calendar.
    Перед використанням переконайся, що credentials.json і token.json присутні.
    Якщо token.json відсутній — потрібно пройти OAuth локально і зберегти token.json.
    """
    if not os.path.exists(CREDENTIALS_FILE):
        logger.error(f"{CREDENTIALS_FILE} не знайдено. Завантаж credentials.json у корінь проєкту.")
        raise FileNotFoundError(f"{CREDENTIALS_FILE} not found")

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        # На Render зазвичай не проходять інтерактивну OAuth-процедуру.
        # Рекомендується локально виконати flow і зберегти token.json у репо/секретах.
        logger.error(f"{TOKEN_FILE} не знайдено. Згенеруй token.json локально через OAuth flow.")
        raise FileNotFoundError(f"{TOKEN_FILE} not found")

    service = build("calendar", "v3", credentials=creds)
    return service


def is_time_slot_available(service, date: datetime.date, time: datetime.time) -> bool:
    """
    Перевіряє, чи вільний слот [start, start+SERVICE_DURATION_MIN) в Google Calendar.
    Повертає True, якщо вільно.
    """
    start_dt = datetime.datetime.combine(date, time, tzinfo=TZ)
    end_dt = start_dt + datetime.timedelta(minutes=SERVICE_DURATION_MIN)
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
        )
        .execute()
    )
    items = events_result.get("items", [])
    return len(items) == 0


def find_alternative_slots(service, date: datetime.date, time: datetime.time, count: int = 3) -> List[str]:
    """
    Шукає альтернативні вільні слоти. Повертає список рядків у форматі "HH:MM".
    Генерує слоти через інтервал SERVICE_DURATION_MIN (тобто зрушує вправо).
    """
    free = []
    start = datetime.datetime.combine(date, time, tzinfo=TZ)
    # перевіримо наступні, скажімо, 12 слотів (або поки не знайдемо необхідну кількість)
    for i in range(1, 25):  # приблизно на 25 * 90 хв — багато варіантів
        candidate = start + datetime.timedelta(minutes=SERVICE_DURATION_MIN * i)
        if candidate.date() != date:
            # якщо перейшли на наступний день — припиняємо
            break
        if is_time_slot_available(service, date, candidate.time()):
            free.append(candidate.time().strftime("%H:%M"))
            if len(free) >= count:
                break
    return free


def create_calendar_event(service, name: str, phone: str, date: datetime.date, time: datetime.time):
    """
    Створює подію в Google Calendar (primary).
    """
    start_dt = datetime.datetime.combine(date, time, tzinfo=TZ)
    end_dt = start_dt + datetime.timedelta(minutes=SERVICE_DURATION_MIN)

    event = {
        "summary": f"📌 Запис S3 Beauty: {name}",
        "description": f"Телефон: {phone} (запис через Telegram бот S3)",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Kyiv"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Kyiv"},
    }
    created = service.events().insert(calendarId="primary", body=event).execute()
    return created

# ----------------- СТАНИ РОЗМОВИ -----------------
NAME, PHONE, DATE, TIME = range(4)

# ----------------- TELEGRAM HANDLERS -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Початкове привітання та запит імені.
    """
    # Клавіатура для швидкого відправлення контакту (якщо хоче)
    contact_button = KeyboardButton("Надіслати контакт", request_contact=True)
    kb = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        "Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME


async def name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    # Запропонуємо надіслати контакт через клавіатуру або ввести вручну
    contact_button = KeyboardButton("Надіслати контакт", request_contact=True)
    kb = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)

    await update.message.reply_text(
        "Приємно познайомитись! 😊\nНадішліть, будь ласка, свій номер телефону або натисніть кнопку нижче, щоб відправити контакт.",
        reply_markup=kb,
    )
    return PHONE


async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Можемо отримати контакт (contact) або текст (номер)
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

    context.user_data["phone"] = phone
    await update.message.reply_text("На яку дату бажаєте записатись? (введіть у форматі РРРР-ММ-ДД)", reply_markup=ReplyKeyboardRemove())
    return DATE


async def date_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        date = datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text("❌ Невірний формат дати. Приклад: 2025-11-05. Спробуйте ще раз:")
        return DATE

    context.user_data["date"] = date
    await update.message.reply_text("⏰ Вкажіть бажаний час у форматі ГГ:ХХ (наприклад 10:00):")
    return TIME


async def time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        time_val = datetime.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await update.message.reply_text("❌ Невірний формат часу. Приклад: 09:30. Спробуйте ще раз:")
        return TIME

    context.user_data["time"] = time_val

    # Отримуємо service (Google Calendar)
    try:
        service = get_calendar_service()
    except FileNotFoundError as e:
        # Якщо token.json або credentials.json відсутні — повідомляємо користувача/лог
        logger.error("Google credentials error: %s", e)
        await update.message.reply_text(
            "⚠️ Неможливо перевірити вільний час — не знайдено Google credentials/token на сервері. Зверніться до адміністратора."
        )
        return ConversationHandler.END

    date = context.user_data["date"]
    time_obj = context.user_data["time"]

    # Перевірка вільного часу
    try:
        free = is_time_slot_available(service, date, time_obj)
    except Exception as e:
        logger.exception("Помилка при перевірці Google Calendar: %s", e)
        await update.message.reply_text("Сталася помилка при перевірці календаря. Спробуйте пізніше.")
        return ConversationHandler.END

    if not free:
        # Знайдемо альтернативні слоти
        alternatives = find_alternative_slots(service, date, time_obj, count=ALTERNATIVE_SLOTS_TO_SHOW)
        if alternatives:
            alt_text = ", ".join(alternatives)
            await update.message.reply_text(
                f"⚠️ На цей час уже є запис. Ось вільні варіанти у той же день: {alt_text}\n"
                "Введіть інший час у форматі ГГ:ХХ або /cancel для скасування."
            )
            return TIME
        else:
            await update.message.reply_text(
                "⚠️ На цей день більше немає вільних слотів близько до вибраного часу. Спробуйте іншу дату."
            )
            return DATE

    # Якщо вільно — створюємо подію
    try:
        created = create_calendar_event(
            service,
            name=context.user_data.get("name", "Клієнт"),
            phone=context.user_data.get("phone", ""),
            date=date,
            time=time_obj,
        )
    except Exception as e:
        logger.exception("Не вдалося створити подію в календарі: %s", e)
        await update.message.reply_text("Сталася помилка при створенні події. Спробуйте пізніше.")
        return ConversationHandler.END

    # Надсилаємо підтвердження — візитка (текст + надсилаємо контакт салону)
    # Надсилаємо текст-підтвердження
    end_dt = datetime.datetime.combine(date, time_obj, tzinfo=TZ) + datetime.timedelta(minutes=SERVICE_DURATION_MIN)
    await update.message.reply_text(
        f"✅ Ваш запис підтверджено!\n\n"
        f"👩‍💼 Ім'я: {context.user_data.get('name')}\n"
        f"📞 Телефон: {context.user_data.get('phone')}\n"
        f"📅 Дата: {date.strftime('%d.%m.%Y')}\n"
        f"⏰ Час: {time_obj.strftime('%H:%M')} — {end_dt.time().strftime('%H:%M')}\n\n"
        "Дякуємо! Чекаємо на Вас у S3 Beauty Salon 💖",
        reply_markup=ReplyKeyboardRemove(),
    )

    # Відправляємо візитку салону (як контакт)
    # Тут вкажи реальний номер та ім'я салону — можна з Environment або хардкод
    salon_phone = os.environ.get("SALON_PHONE", "+380991234567")
    salon_name = os.environ.get("SALON_NAME", "S3 Beauty Salon")
    # За допомогою send_contact — бот надсилає контакт у чат
    await context.bot.send_contact(
        chat_id=update.effective_chat.id,
        phone_number=salon_phone,
        first_name=salon_name,
    )

    # Можемо також надіслати посилання на подію (якщо потрібно)
    event_link = created.get("htmlLink")
    if event_link:
        await update.message.reply_text(f"Посилання на запис у календарі: {event_link}")

    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано ✅", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ----------------- FLASK + TELEGRAM SETUP -----------------
app = Flask(__name__)

# Створюємо Application (PTB v20)
application = Application.builder().token(TOKEN).build()

# Conversation handler
conv = ConversationHandler(
    entry_points=[CommandHandler("start", start_handler)],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_handler)],
        PHONE: [
            # accept contact or phone text
            MessageHandler(filters.CONTACT, phone_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler),
        ],
        DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, date_handler)],
        TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, time_handler)],
    },
    fallbacks=[CommandHandler("cancel", cancel_handler)],
    allow_reentry=True,
)
application.add_handler(conv)


# Вебхук маршрут — використовуємо async view (Flask[async] має бути встановлено)
@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.exception("Помилка при обробці оновлення: %s", e)
    return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "🤖 S3 Beauty Bot — працює", 200


# ----------------- RUN (в режимі webhook) -----------------
if __name__ == "__main__":
    # Ставимо webhook при запуску (Render дає змінну RENDER_EXTERNAL_HOSTNAME)
    external_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if external_host:
        webhook_url = f"https://{external_host}/{TOKEN}"
    else:
        # Для локальної розробки fallback (не для production)
        webhook_url = os.environ.get("WEBHOOK_URL", f"https://example.com/{TOKEN}")

    # Встановлюємо webhook
    try:
        # set_webhook через Bot
        bot = application.bot
        bot.set_webhook(webhook_url)
        logger.info("Webhook встановлено: %s", webhook_url)
    except Exception as e:
        logger.exception("Не вдалося встановити webhook: %s", e)

    # Запускаємо Flask (Render запустить цю програму на потрібному порту)
    port = int(os.environ.get("PORT", 10000))
    # Note: Flask[async] повинен бути встановлений (Flask[async]==3.1.2 в requirements)
    app.run(host="0.0.0.0", port=port)
