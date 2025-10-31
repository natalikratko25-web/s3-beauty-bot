import os
import logging
import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# -----------------------
# Налаштування логування
# -----------------------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -----------------------
# Константи / стани
# -----------------------
SCOPES = ['https://www.googleapis.com/auth/calendar.events']
STATE_NAME, STATE_DATE, STATE_TIME, STATE_PHONE = range(4)
TIMEZONE = 'Europe/Kiev'  # використовується при створенні події
SLOT_MINUTES = 90  # 1.5 години

# -----------------------
# Допоміжні функції
# -----------------------
def get_calendar_service():
    """
    Повертає об'єкт Google Calendar service.
    Очікує, що token.json та credentials.json присутні у робочій директорії.
    У середовищах без браузера (як Render) не намагається відкривати InstalledAppFlow.
    """
    if not os.path.exists('token.json'):
        logger.error("token.json не знайдено. Завантажте token.json на сервер або як Secret File.")
        raise FileNotFoundError("token.json not found")

    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        # на сервері ми не можемо відкривати браузер — повідомляємо про помилку
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.exception("Не вдалося оновити токен: %s", e)
                raise
        else:
            logger.error("Credentials невалідні або немає refresh token. Не можна виконати авторизацію на сервері.")
            raise RuntimeError("Invalid Google credentials on server")
    service = build('calendar', 'v3', credentials=creds)
    return service

def format_iso_with_tz(dt: datetime.datetime) -> str:
    """Повертає ISO-строку з +02:00 (зона Europe/Kiev)."""
    # переконаємося, що це naive datetime, потім додаємо +02:00 у рядок
    return dt.isoformat() + '+02:00'

def is_time_slot_available(service, date_obj: datetime.date, time_obj: datetime.time) -> bool:
    """
    Перевіряє, чи є події у проміжку [start, start + SLOT_MINUTES).
    Повертає True, якщо слот вільний.
    """
    start_dt = datetime.datetime.combine(date_obj, time_obj)
    end_dt = start_dt + datetime.timedelta(minutes=SLOT_MINUTES)
    time_min = format_iso_with_tz(start_dt)
    time_max = format_iso_with_tz(end_dt)

    try:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
    except HttpError as e:
        logger.exception("HTTP error при запиті events.list: %s", e)
        raise
    except Exception as e:
        logger.exception("Інша помилка при перевірці слоту: %s", e)
        raise

    items = events_result.get('items', [])
    return len(items) == 0

# -----------------------
# Обробники бот-діалогу
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Вітаю 💅 Я — бот салону краси S3. Давайте знайомитися! Як вас звати?"
    )
    return STATE_NAME

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text.strip()
    await update.message.reply_text("Чудово! На яку дату бажаєте записатися? (формат YYYY-MM-DD)")
    return STATE_DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        date_obj = datetime.datetime.strptime(text, "%Y-%m-%d").date()
        context.user_data['date'] = date_obj
        await update.message.reply_text("Вкажіть, будь ласка, час (формат HH:MM, наприклад 14:30)")
        return STATE_TIME
    except ValueError:
        await update.message.reply_text("Неправильний формат дати. Спробуйте ще (YYYY-MM-DD).")
        return STATE_DATE

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        time_obj = datetime.datetime.strptime(text, "%H:%M").time()
        context.user_data['time'] = time_obj
        await update.message.reply_text("Дякую! Тепер залиште, будь ласка, номер телефону для підтвердження (наприклад +380971234567).")
        return STATE_PHONE
    except ValueError:
        await update.message.reply_text("Невірний формат часу. Введіть у форматі HH:MM (наприклад 15:30).")
        return STATE_TIME

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data['phone'] = phone

    name = context.user_data.get('name')
    date_obj = context.user_data.get('date')
    time_obj = context.user_data.get('time')

    # Коротке підтвердження, щоб користувач знав, що йде обробка
    await update.message.reply_text("🔎 Перевіряю доступність часу та створюю запис — трохи зачекайте...")

    # Отримуємо сервіс календаря
    try:
        service = get_calendar_service()
    except FileNotFoundError:
        await update.message.reply_text("⚠️ Помилка: token.json не знайдено на сервері. Будь ласка, завантажте token.json як Secret File на Render.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Не вдалося ініціалізувати Calendar service: %s", e)
        await update.message.reply_text("⚠️ Помилка авторизації Google Calendar. Спробуйте пізніше.")
        return ConversationHandler.END

    # Перевірка доступності слоту
    try:
        available = is_time_slot_available(service, date_obj, time_obj)
    except Exception as e:
        logger.exception("Помилка при перевірці слоту: %s", e)
        await update.message.reply_text("⚠️ Сталася помилка при перевірці вільного часу. Спробуйте ще раз пізніше.")
        return ConversationHandler.END

    if not available:
        # Згенеруємо кілька альтернативних варіантів
        free_slots = []
        start_dt = datetime.datetime.combine(date_obj, time_obj)
        for i in range(1, 8):  # перевірити наступні слоти
            candidate = (start_dt + datetime.timedelta(minutes=SLOT_MINUTES * i)).time()
            try:
                if is_time_slot_available(service, date_obj, candidate):
                    free_slots.append(candidate.strftime("%H:%M"))
                if len(free_slots) >= 3:
                    break
            except Exception:
                # якщо є помилка при перевірці кожного слоту — ігноруємо його і продовжуємо
                logger.exception("Помилка при перевірці альтернативного слоту для %s", candidate)
                continue

        if free_slots:
            await update.message.reply_text("⏰ На обраний час вже є запис. Ось кілька вільних варіантів: " + ", ".join(free_slots))
        else:
            await update.message.reply_text("⏰ На цей день/час немає вільних слотів. Спробуйте іншу дату або зверніться до салону.")
        return ConversationHandler.END

    # Якщо вільно — створюємо подію
    start_dt = datetime.datetime.combine(date_obj, time_obj)
    end_dt = start_dt + datetime.timedelta(minutes=SLOT_MINUTES)

    event_body = {
        'summary': f'Запис S3 — {name}',
        'description': f'Телефон: {phone}',
        'start': {'dateTime': start_dt.isoformat(), 'timeZone': TIMEZONE},
        'end': {'dateTime': end_dt.isoformat(), 'timeZone': TIMEZONE},
    }

    try:
        service.events().insert(calendarId='primary', body=event_body).execute()
    except HttpError as e:
        logger.exception("HttpError при створенні події: %s", e)
        await update.message.reply_text("⚠️ Не вдалося створити подію в календарі. Спробуйте пізніше.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Інша помилка при створенні події: %s", e)
        await update.message.reply_text("⚠️ Сталася помилка при створенні запису. Спробуйте пізніше.")
        return ConversationHandler.END

    # Надсилаємо візитку підтвердження
    confirmation = (
        "✨ *Ваш запис підтверджено!* ✨\n\n"
        f"👤 *Ім'я:* {name}\n"
        f"📅 *Дата:* {date_obj.strftime('%d.%m.%Y')}\n"
        f"⏰ *Час:* {time_obj.strftime('%H:%M')} — {end_dt.time().strftime('%H:%M')}\n"
        f"📞 *Телефон:* {phone}\n\n"
        "💖 Чекаємо на вас у *S3 Beauty Salon*! \n_Естетика в кожній деталі._"
    )
    await update.message.reply_text(confirmation, parse_mode='Markdown')

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Запис скасовано. Якщо передумаєте — напишіть /start.")
    return ConversationHandler.END

# -----------------------
# Точка входу
# -----------------------
def main():
    TOKEN = os.getenv('BOT_TOKEN')
    if not TOKEN:
        logger.error("BOT_TOKEN не знайдено у змінних середовища. Додайте змінну BOT_TOKEN у Render Environment.")
        print("BOT_TOKEN not set. Please add it to environment variables.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            STATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            STATE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date)],
            STATE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time)],
            STATE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    app.add_handler(conv)

    logger.info("Бот запускається...")
    print("Бот запускається...")  # корисно в логах Render
    app.run_polling()

if __name__ == '__main__':
    main()



