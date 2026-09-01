"""
Grow And Sell — бот-информатор с подпиской через /start
---------------------------------------------------------
Работает по расписанию (GitHub Actions), без постоянно висящего
сервера. При каждом запуске скрипт:

  1. Спрашивает у Telegram, не написал ли кто-то боту (getUpdates).
     Если кто-то нажал Start / прислал любое сообщение — добавляет
     его chat_id в список подписчиков (хранится в самой Firebase,
     в announcerBotState/subscribers) и присылает приветствие.
  2. Проверяет изменения в игре: ивенты, промокоды, таблицу лидеров.
  3. Рассылает найденные изменения ВСЕМ подписчикам.
  4. Сохраняет обновлённое состояние и завершает работу.

Никакого отдельного "канала" не нужно — бот сам собирает аудиторию
из тех, кто ему написал.

НАСТРОЙКА
---------
Переменные окружения (в GitHub Actions — Secrets):

    BOT_TOKEN                       — токен Telegram-бота
    DATABASE_URL                    — https://<project-id>-default-rtdb...
    FIREBASE_SERVICE_ACCOUNT_JSON   — содержимое serviceAccountKey.json целиком
    LEADERBOARD_INTERVAL_HOURS      — необязательно, по умолчанию 24
    LEADERBOARD_TOP_N               — необязательно, по умолчанию 10
"""

import os
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, db

# ================== CONFIG (из переменных окружения) ==================
BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]
SERVICE_ACCOUNT_JSON = os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"]

LEADERBOARD_INTERVAL_HOURS = float(os.environ.get("LEADERBOARD_INTERVAL_HOURS", "24"))
LEADERBOARD_TOP_N = int(os.environ.get("LEADERBOARD_TOP_N", "10"))
# =========================================================================

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_PATH = "announcerBotState"  # узел в Firebase, где бот хранит своё состояние

WELCOME_TEXT = (
    "👋 Привет! Я бот-информатор Grow And Sell.\n\n"
    "Буду присылать сюда:\n"
    "🪩 начало и конец ивентов\n"
    "🎁 новые промокоды\n"
    "🏆 таблицу лидеров\n\n"
    "Ничего делать не нужно — просто жди сообщений."
)

EVENT_NAMES = {
    "disco":         ("🪩", "Диско-пати"),
    "luck":          ("🍀", "Удача"),
    "luckyRain":     ("🌧️", "Дождь удачи"),
    "mutationSurge": ("🧬", "Всплеск мутаций"),
    "ramadan":       ("🌙", "Рамадан"),
    "sakura":        ("🌸", "Сакура"),
    "birthday":      ("🎂", "День рождения"),
    "nyan":          ("🐱", "Nyan-ивент"),
    "autumn":        ("🍂", "Осень"),
    "blackHole":     ("🕳️", "Чёрная дыра"),
    "flood":         ("🌊", "Потоп"),
    "rocket":        ("🚀", "Ракета (командная цель)"),
    "instantGrow":   ("⚡", "Мгновенный рост"),
}


# ---------------------------------------------------------------- Telegram

def send_to(chat_id, text: str) -> bool:
    """Отправляет сообщение одному chat_id. Возвращает False, если бот
    заблокирован этим пользователем (403) — тогда его надо отписать."""
    resp = requests.post(f"{API_URL}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    })
    if resp.ok:
        return True

    print(f"Не удалось отправить {chat_id}:", resp.text)
    try:
        blocked = resp.json().get("error_code") == 403
    except Exception:
        blocked = False
    return not blocked  # True = оставить в подписчиках, False = отписать


def broadcast(text: str, subscribers: list) -> list:
    """Рассылает текст всем подписчикам, возвращает обновлённый список
    (без тех, кто успел заблокировать бота)."""
    alive = []
    for chat_id in subscribers:
        if send_to(chat_id, text):
            alive.append(chat_id)
    return alive


def check_new_subscribers(state: dict) -> dict:
    """Спрашивает у Telegram новые сообщения; каждого нового отправителя
    добавляет в подписчики и шлёт ему приветствие."""
    offset = state.get("lastUpdateId", 0) + 1
    subscribers = set(state.get("subscribers", []))

    resp = requests.get(f"{API_URL}/getUpdates", params={"offset": offset, "timeout": 0})
    if not resp.ok:
        print("Ошибка getUpdates:", resp.text)
        return state

    updates = resp.json().get("result", [])
    max_update_id = state.get("lastUpdateId", 0)

    for upd in updates:
        max_update_id = max(max_update_id, upd["update_id"])
        message = upd.get("message") or upd.get("edited_message")
        if not message:
            continue
        chat_id = message.get("chat", {}).get("id")
        if chat_id is None:
            continue

        if chat_id not in subscribers:
            subscribers.add(chat_id)
            send_to(chat_id, WELCOME_TEXT)

    state["lastUpdateId"] = max_update_id
    state["subscribers"] = list(subscribers)
    return state


# ------------------------------------------------------------------ Firebase

def load_state() -> dict:
    return db.reference(STATE_PATH).get() or {}


def save_state(state: dict):
    db.reference(STATE_PATH).set(state)


def check_events(state: dict):
    """Возвращает (обновлённый state, список текстов для рассылки)."""
    snapshot = db.reference("events").get() or {}
    seen = state.get("seenEventsActive", {})
    initialized = state.get("eventsInitialized", False)
    messages = []

    for key, (emoji, title) in EVENT_NAMES.items():
        info = snapshot.get(key)
        is_active = bool(info and info.get("active"))
        was_active = seen.get(key, False)

        if initialized:
            if is_active and not was_active:
                messages.append(f"{emoji} <b>Внимание!</b> Начался ивент «{title}»!")
            elif was_active and not is_active:
                messages.append(f"⏹️ Ивент «{title}» завершён.")

        seen[key] = is_active

    state["seenEventsActive"] = seen
    state["eventsInitialized"] = True
    return state, messages


def check_codes(state: dict):
    snapshot = db.reference("dynamicCodes").get() or {}
    known = set(state.get("knownCodes", []))
    initialized = state.get("codesInitialized", False)
    messages = []

    if not initialized:
        known |= set(snapshot.keys())
        state["knownCodes"] = list(known)
        state["codesInitialized"] = True
        return state, messages

    for code, info in snapshot.items():
        if code in known:
            continue
        known.add(code)

        info = info if isinstance(info, dict) else {}
        max_uses = info.get("maxUses")
        uses = info.get("uses", 0)
        limit_text = f"{uses}/{max_uses} активаций" if max_uses else "без лимита"

        messages.append(f"🎁 <b>Новый промокод:</b> <code>{code}</code> — {limit_text}")

    state["knownCodes"] = list(known)
    return state, messages


def maybe_post_leaderboard(state: dict):
    last_ts = state.get("lastLeaderboardTs", 0)
    now = time.time()
    messages = []

    if now - last_ts < LEADERBOARD_INTERVAL_HOURS * 3600:
        return state, messages

    data = db.reference("leaderboard").get() or {}
    tags = db.reference("tags").get() or {}

    by_money = sorted(
        data.items(), key=lambda kv: (kv[1] or {}).get("money", 0), reverse=True
    )[:LEADERBOARD_TOP_N]

    lines = ["🏆 <b>Таблица лидеров</b>"]
    for i, (pid, p) in enumerate(by_money, start=1):
        p = p or {}
        name = tags.get(pid, {}).get("text") or pid[:6]
        money = p.get("money", 0)
        lines.append(f"{i}. {name} — 💰 {money:,}".replace(",", " "))

    messages.append("\n".join(lines))
    state["lastLeaderboardTs"] = now
    return state, messages


def main():
    cred = credentials.Certificate(json.loads(SERVICE_ACCOUNT_JSON))
    firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

    state = load_state()
    state = check_new_subscribers(state)

    all_messages = []
    state, msgs = check_events(state)
    all_messages += msgs
    state, msgs = check_codes(state)
    all_messages += msgs
    state, msgs = maybe_post_leaderboard(state)
    all_messages += msgs

    subscribers = state.get("subscribers", [])
    for text in all_messages:
        subscribers = broadcast(text, subscribers)
    state["subscribers"] = subscribers

    save_state(state)
    print(f"Готово. Подписчиков: {len(subscribers)}. Сообщений разослано: {len(all_messages)}.")


if __name__ == "__main__":
    main()
