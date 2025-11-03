# bot.py — повний робочий бот для S3 Beauty Salon
# Вимоги: python-3.11.x, Flask[async], python-telegram-bot[webhooks], google-api-python-client, google-auth-oauthlib

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

# Google API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow

# ---------------- CONFIG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN")  # ОБОВ'ЯЗКОВО: прописати в змінних оточення на Render
if not TOKEN:
    logger.error("BOT_TOKEN не знайдено в змінних оточення. Додай BOT_TOKEN.")
    # Ми не кидаємо помилку, щоб при локальному імпорті не ламалось, але bot не запуститься без токену.

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TZ = ZoneInfo("Europe/Kyiv")  # часовий пояс (використовується для isoformat з часовою зоною)
APPOINTMENT_MINUTES = 90  # 1.5 години

# ---------------- Google Calendar helpers ----------------
def get_calendar_service():
    """
    Повертає сервіс Google Calendar.
    Потребує credentials.json у корені проєкту або token.json (вже авторизований).
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        # Якщо token.json немає — спробуємо локальну авторизацію (він відкриє браузер локально).
        # На сервері краще мати token.json вже готовий.
        if not os.path.exists("credentials.json"):
            raise FileNotFoundError("credentials.json не знайдено. Додайте файл OAuth2 client secret.")
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    service = build("calendar", "v3", credentials=creds)
    return service

def is_time_slot_available(service, date_obj: datetime.date, time_obj: datetime.time) -> bool:
    """
    Перевіряє чи вільний слот (з врахуванням тривалості APPOINTMENT_MINUTES).
    Використовує часовий пояс TZ.
    """
    start_dt = datetime.datetime.combine(date_obj, time_obj).replace(tzinfo=TZ)
    end_dt = start_dt + datetime.timedelta(minutes=APPOINTMENT_MINUTES)

    # Google Calendar очікує RFC3339 з часовою зоною
    time_min = start_dt.isoformat()
    time_max = end_dt.isoformat()

    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    items = events_result.get("items", [])
    return len(items) == 0

def find_alternative_slots(service, date_obj: datetime.date, start_time_obj: datetime.time, max_suggestions=3):
    """
    Повертає список до max_suggestions доступних альтернатив, починаючи з часу
    через APPOINTMENT_MINUTES кроки (шукаємо вперед).
    """
    suggestions = []
    base_dt = datetime.datetime.combine(date_obj, start_time_obj).replace(tzinfo=TZ)
    step = datetime.timedelta(minutes=APPOINTMENT_MINUTES)
    # шукатимемо наступні 10 слотів максимум
    for i in range(1, 15):
        candidate_dt = base_dt + step * i
        candidate_date = candidate_dt.date()
        candidate_time = candidate_dt.time()
        if is_time_slot_available(service, candidate_date, candidate_time):
            suggestions.append(candidate_time.strftime("%H:%M"))
        if len(suggestions) >= max_suggestions:
            break
    return suggestions

# ---------------- Conversation states ----------------
NAME, PHONE, DATE, TIME = range(4)

# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початкова команда — привітання і запит імені."""
    await update.message.reply_text(
        "Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?"
    )
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("Приємно познайомитись! 😊\nА який ваш номер телефону?")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_phone = update.message.text.strip()
    # простий clean: лишаємо цифри та плюс
    clean = "".join(ch for ch in raw_phone if ch.isdigit() or ch == "+")
    context.user_data["phone"] = clean or raw_phone
    await update.message.reply_text("На яку дату бажаєте записатись? (в форматі YYYY-MM-DD)")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        date_obj = datetime.datetime.strptime(text, "%Y-%m-%d").date()
        context.user_data["date"] = date_obj
        await update.message.reply_text("Вкажіть бажаний час (формат HH:MM, 24-годинний):")
        return TIME
    except ValueError:
        await update.message.reply_text("❌ Невірний формат дати. Введіть у форматі YYYY-MM-DD.")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        time_obj = datetime.datetime.strptime(text, "%H:%M").time()
        context.user_data["time"] = time_obj

        # підключаємо сервіс календаря
        try:
            service = get_calendar_service()
        except Exception as e:
            logger.exception("Помилка отримання сервісу календаря")
            await update.message.reply_text(
                "🧩 Потрібна авторизація Google Calendar. "
                "На сервері немає token.json або credentials.json. "
                "Будь ласка, пройдіть авторизацію локально та завантажте token.json, "
                "або перевірте credentials.json."
            )
            return ConversationHandler.END

        date_obj = context.user_data["date"]

        # Перевірка вільного часу
        if not is_time_slot_available(service, date_obj, time_obj):
            # знайдемо альтернативи
            alts = find_alternative_slots(service, date_obj, time_obj, max_suggestions=3)
            msg = "⚠️ На цей час уже є запис."
            if alts:
                msg += f" Ось вільні варіанти: {', '.join(alts)}"
            else:
                msg += " Нема вільних варіантів найближчим часом."
            await update.message.reply_text(msg)
            # залишаємо користувача в стані TIME (щоб повторив ввід)
            return TIME

        # Якщо вільно — створюємо подію
        start_dt = datetime.datetime.combine(date_obj, time_obj).replace(tzinfo=TZ)
        end_dt = start_dt + datetime.timedelta(minutes=APPOINTMENT_MINUTES)

        event = {
            "summary": f"Запис у S3 Beauty Salon — {context.user_data['name']}",
            "description": f"Ім'я: {context.user_data['name']}\nТелефон: {context.user_data['phone']}",
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
            "reminders": {"useDefault": True},
        }

        created = service.events().insert(calendarId="primary", body=event).execute()
        logger.info("Created event: %s", created.get("htmlLink"))

        # Відправляємо підтвердження + контакт (візитка)
        name = context.user_data["name"]
        phone = context.user_data["phone"]
        date_str = date_obj.strftime("%d.%m.%Y")
        time_str = start_dt.strftime("%H:%M")
        end_time_str = end_dt.strftime("%H:%M")

        # Формуємо просту vCard (рядок)
        vcard = (
            "BEGIN:VCARD\n"
            "VERSION:3.0\n"
            f"N:{name}\n"
            f"FN:{name}\n"
            f"ORG:S3 Beauty Salon\n"
            f"TEL;TYPE=CELL:{phone}\n"
            "END:VCARD"
        )

        # Надсилаємо текст-підтвердження
        await update.message.reply_text(
            "✨ Ваш запис підтверджено!\n\n"
            f"👩 Ім'я: {name}\n"
            f"📞 Телефон: {phone}\n"
            f"📅 Дата: {date_str}\n"
            f"⏰ Час: {time_str} — {end_time_str}\n\n"
            "Дякуємо! Чекаємо на вас у S3 Beauty Salon 💖"
        )

        # Надсилаємо візитку-контакт (як contact + vCard)
        # Використаємо reply_contact якщо доступний
        try:
            # send contact (vcard як додатковий параметр)
            await update.message.reply_contact(phone_number=phone, first_name=name, vcard=vcard)
        except Exception:
            # якщо не вдалось — принаймні надішлемо vCard текстом
            await update.message.reply_text(f"vCard:\n{vcard}")

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("❌ Невірний формат часу. Введіть у форматі HH:MM (24h).")
        return TIME

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано ✅")
    return ConversationHandler.END

# ---------------- Telegram + Flask setup ----------------
def create_app():
    # створюємо Application (telegram)
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не встановлено як змінна оточення.")

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=300,
    )

    application.add_handler(conv_handler)

    # Flask app
    flask_app = Flask(__name__)

    # async Flask view (Flask[async] required)
    @flask_app.post(f"/{TOKEN}")
    async def webhook():
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        # process the incoming update
        await application.process_update(update)
        return "OK", 200

    @flask_app.get("/")
    def index():
        return "🤖 S3 Beauty Bot — S3 Beauty Salon", 200

    # attach telegram app to flask app for external access if потрібно
    flask_app.telegram_app = application
    return flask_app, application

app, telegram_app = create_app()

# ---------------- Entrypoint для запуску на Render ----------------
if __name__ == "__main__":
    # Перед запуском Flask локально можна встановити webhook (необов'язково — можна зробити зовні)
    RENDER_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if RENDER_HOSTNAME:
        webhook_url = f"https://{RENDER_HOSTNAME}/{TOKEN}"
        # ставимо webhook асинхронно
        import asyncio

        async def set_hook():
            try:
                await telegram_app.bot.set_webhook(url=webhook_url)
                logger.info("Webhook встановлено: %s", webhook_url)
            except Exception as e:
                logger.exception("Не вдалось встановити webhook: %s", e)

        asyncio.run(set_hook())
    else:
        logger.info("RENDER_EXTERNAL_HOSTNAME не задано — пропускаємо автоматичне встановлення webhook.")

    # запускаємо Flask (в Render цей блок не використовуватиметься — Render запускає як web service)
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
