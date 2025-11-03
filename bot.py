#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import logging
import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request
import requests

# Google
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- НАЛАШТУВАННЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("ERROR: set BOT_TOKEN environment variable (Telegram bot token)")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
WEBHOOK_PATH = f"/{BOT_TOKEN}"  # endpoint для telegram webhook
PORT = int(os.environ.get("PORT", 10000))

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TZ = ZoneInfo("Europe/Kyiv")  # використовується для isoformat з часовою зоною

# Простий in-memory state (chat_id -> dict)
user_states = {}  # { chat_id: {"state": "NAME"|"PHONE"|"DATE"|"TIME", "data": {...}} }

# ---------- Google Calendar helpers ----------
def get_calendar_service():
    """Повертає google calendar service. Якщо немає token.json — виконає flow локально (відкриє браузер)."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        if not os.path.exists("credentials.json"):
            raise FileNotFoundError("credentials.json not found. Place your OAuth client credentials in credentials.json")
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        # Це відкриє локальний браузер; працює якщо ви запускаєте локально.
        creds = flow.run_local_server(port=0)
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def is_time_slot_available(service, date_obj, time_obj, duration_minutes=90):
    """Перевіряє, чи є події між start і end."""
    start_dt = datetime.datetime.combine(date_obj, time_obj).replace(tzinfo=TZ)
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
    events_result = service.events().list(
        calendarId="primary",
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=1,
    ).execute()
    return len(events_result.get("items", [])) == 0

def create_calendar_event(service, name, phone, date_obj, time_obj, duration_minutes=90):
    start_dt = datetime.datetime.combine(date_obj, time_obj).replace(tzinfo=TZ)
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
    event = {
        "summary": f"💅 Запис у S3 Beauty Salon ({name})",
        "description": f"Телефон: {phone}",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Kyiv"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Kyiv"},
    }
    created = service.events().insert(calendarId="primary", body=event).execute()
    return created

# ---------- Telegram helpers ----------
def send_message(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    resp = requests.post(f"{TELEGRAM_API}/sendMessage", data=data)
    logger.info("sendMessage (%s) -> %s", resp.status_code, resp.text)
    return resp

def start_conversation(chat_id):
    user_states[chat_id] = {"state": "NAME", "data": {}}
    send_message(chat_id, "Вітаю 💅 Давайте знайомитися. Я бот салону краси S3!\nА як вас звати?")

# ---------- Flask app (webhook) ----------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return "S3 Beauty Bot працює.", 200

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    logger.info("Update: %s", update)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return "ok", 200

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    if text and text.strip().lower() == "/start":
        start_conversation(chat_id)
        return "ok", 200

    if chat_id not in user_states:
        send_message(chat_id, "Напишіть /start щоб почати запис.")
        return "ok", 200

    # керуємо станами
    state = user_states[chat_id]["state"]
    data = user_states[chat_id]["data"]

    try:
        if state == "NAME":
            data["name"] = text.strip()
            user_states[chat_id]["state"] = "PHONE"
            send_message(chat_id, "Приємно познайомитись! 😊\nБудь ласка, надішліть свій номер телефону (наприклад +380XXXXXXXXX).")
            return "ok", 200

        if state == "PHONE":
            data["phone"] = text.strip()
            user_states[chat_id]["state"] = "DATE"
            send_message(chat_id, "На яку дату бажаєте записатись? Введіть у форматі РРРР-ММ-ДД (наприклад 2025-11-05).")
            return "ok", 200

        if state == "DATE":
            try:
                date_obj = datetime.datetime.strptime(text.strip(), "%Y-%m-%d").date()
                data["date"] = date_obj
                user_states[chat_id]["state"] = "TIME"
                send_message(chat_id, "⏰ Вкажіть бажаний час у форматі ГГ:ХХ (24-годинний, наприклад 10:30). Тривалість — 1.5 години.")
            except ValueError:
                send_message(chat_id, "❌ Невірний формат дати. Введіть у форматі РРРР-ММ-ДД.")
            return "ok", 200

        if state == "TIME":
            try:
                time_obj = datetime.datetime.strptime(text.strip(), "%H:%M").time()
                data["time"] = time_obj

                # підключаємо календар (якщо token.json відсутній — буде просити OAuth)
                try:
                    service = get_calendar_service()
                except FileNotFoundError as e:
                    send_message(chat_id, "🧩 Потрібна авторизація Google Calendar. Запустіть бота локально і пройдіть OAuth (створиться token.json) або завантажте token.json на сервер. " + str(e))
                    return "ok", 200
                except Exception as e:
                    logger.exception("Calendar connection failed")
                    send_message(chat_id, "Помилка підключення до Google Calendar: " + str(e))
                    return "ok", 200

                # перевірка вільного слоту
                if not is_time_slot_available(service, data["date"], data["time"], duration_minutes=90):
                    # запропонувати кілька варіантів
                    free_slots = []
                    base_dt = datetime.datetime.combine(data["date"], data["time"]).replace(tzinfo=TZ)
                    for i in range(1, 8):
                        cand = base_dt + datetime.timedelta(minutes=90 * i)
                        if is_time_slot_available(service, cand.date(), cand.time(), duration_minutes=90):
                            free_slots.append(cand.strftime("%Y-%m-%d %H:%M"))
                        if len(free_slots) >= 3:
                            break
                    if free_slots:
                        send_message(chat_id, "⚠️ На цей час вже є запис. Ось вільні варіанти:\n" + "\n".join(free_slots))
                    else:
                        send_message(chat_id, "⚠️ На цей час вже є запис й я не знайшов найближчих вільних слотів. Спробуйте іншу дату або час.")
                    # залишаємо стан TIME
                    return "ok", 200

                # якщо вільно — створюємо подію
                created = create_calendar_event(service, data["name"], data["phone"], data["date"], data["time"], duration_minutes=90)
                end_time = (datetime.datetime.combine(data["date"], data["time"]) + datetime.timedelta(minutes=90)).time()

                send_message(chat_id,
                    "✨ Запис підтверджено!\n\n"
                    f"👩‍💼 Ім'я: {data['name']}\n"
                    f"📞 Телефон: {data['phone']}\n"
                    f"📅 Дата: {data['date'].strftime('%d.%m.%Y')}\n"
                    f"⏰ Час: {data['time'].strftime('%H:%M')} - {end_time.strftime('%H:%M')}\n\n"
                    f"Номер події в Google Calendar: {created.get('id', '—')}"
                )

                # візитка-підтвердження (VCARD-like текст)
                vcard = (
                    "BEGIN:VCARD\nVERSION:3.0\n"
                    f"N:{data['name']}\nTEL:{data['phone']}\nORG:S3 Beauty Salon\nEND:VCARD"
                )
                send_message(chat_id, "Візитка підтвердження (VCARD):\n" + vcard)

                # очищаємо стан користувача
                user_states.pop(chat_id, None)
            except ValueError:
                send_message(chat_id, "❌ Невірний формат часу. Введіть у форматі ГГ:ХХ (наприклад 14:30).")
            return "ok", 200

    except Exception as e:
        logger.exception("Unexpected error")
        send_message(chat_id, "Виникла помилка. Спробуйте пізніше.")
        return "ok", 200

    return "ok", 200

if __name__ == "__main__":
    logger.info("Запускаю Flask на порту %s, webhook path: %s", PORT, WEBHOOK_PATH)
    app.run(host="0.0.0.0", port=PORT)
