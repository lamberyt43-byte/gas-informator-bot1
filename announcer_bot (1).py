"""
Grow And Sell — бот-информатор для чата канала
(версия для запуска ПО РАСПИСАНИЮ, например в GitHub Actions)
------------------------------------------------------------
В отличие от исходной версии, этот скрипт не "слушает" Firebase
постоянно, а запускается разово: читает текущее состояние базы,
сравнивает его с тем, что было в прошлый запуск (это состояние
хранится в самой Firebase, в узле announcerBotState), постит в
Telegram то, что изменилось, сохраняет новое состояние — и
завершает работу. Между запусками ничего не должно постоянно
работать — GitHub Actions сам поднимает и опускает контейнер по
расписанию (cron), поэтому не нужен ни сервер, ни включённый
компьютер.

НАСТРОЙКА
---------
Все параметры берутся из переменных окружения (в GitHub Actions
это Secrets — см. announcer.yml и SETUP.md):

    BOT_TOKEN                       — токен Telegram-бота
    CHANNEL_ID                      — @username канала или числовой chat_id
    DATABASE_URL                    — https://<project-id>-default-rtdb...
    FIREBASE_SERVICE_ACCOUNT_JSON   — содержимое serviceAccountKey.json
                                       ЦЕЛИКОМ, одной строкой (весь JSON)
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
CHANNEL_ID = os.environ["CHANNEL_ID"]
DATABASE_URL = os.environ["DATABASE_URL"]
SERVICE_ACCOUNT_JSON = os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"]

LEADERBOARD_INTERVAL_HOURS = float(os.environ.get("LEADERBOARD_INTERVAL_HOURS", "24"))
LEADERBOARD_TOP_N = int(os.environ.get("LEADERBOARD_TOP_N", "10"))
# =========================================================================

STATE_PATH = "announcerBotState"  # узел в Firebase, где бот хранит своё состояние

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


def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
    })
    if not resp.ok:
        print("Ошибка отправки в Telegram:", resp.text)


def load_state() -> dict:
    return db.reference(STATE_PATH).get() or {}


def save_state(state: dict):
    db.reference(STATE_PATH).set(state)


def check_events(state: dict) -> dict:
    """Сравнивает текущие активные ивенты с прошлым запуском, постит изменения."""
    snapshot = db.reference("events").get() or {}
    seen = state.get("seenEventsActive", {})
    initialized = state.get("eventsInitialized", False)

    for key, (emoji, title) in EVENT_NAMES.items():
        info = snapshot.get(key)
        is_active = bool(info and info.get("active"))
        was_active = seen.get(key, False)

        # При самом первом запуске просто запоминаем состояние,
        # не анонсируя уже идущие ивенты как "начавшиеся".
        if initialized:
            if is_active and not was_active:
                send_message(f"{emoji} <b>Внимание!</b> Начался ивент «{title}»!")
            elif was_active and not is_active:
                send_message(f"⏹️ Ивент «{title}» завершён.")

        seen[key] = is_active

    state["seenEventsActive"] = seen
    state["eventsInitialized"] = True
    return state


def check_codes(state: dict) -> dict:
    """Сравнивает список промокодов с прошлым запуском, анонсирует новые."""
    snapshot = db.reference("dynamicCodes").get() or {}
    known = set(state.get("knownCodes", []))
    initialized = state.get("codesInitialized", False)

    if not initialized:
        # Начальная синхронизация — просто запоминаем уже существующие
        # коды, чтобы не анонсировать их как "новые".
        known |= set(snapshot.keys())
        state["knownCodes"] = list(known)
        state["codesInitialized"] = True
        return state

    for code, info in snapshot.items():
        if code in known:
            continue
        known.add(code)

        info = info if isinstance(info, dict) else {}
        max_uses = info.get("maxUses")
        uses = info.get("uses", 0)
        limit_text = f"{uses}/{max_uses} активаций" if max_uses else "без лимита"

        send_message(f"🎁 <b>Новый промокод:</b> <code>{code}</code> — {limit_text}")

    state["knownCodes"] = list(known)
    return state


def maybe_post_leaderboard(state: dict) -> dict:
    """Постит таблицу лидеров, если прошло достаточно времени с прошлого раза."""
    last_ts = state.get("lastLeaderboardTs", 0)
    now = time.time()

    if now - last_ts < LEADERBOARD_INTERVAL_HOURS * 3600:
        return state

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

    send_message("\n".join(lines))
    state["lastLeaderboardTs"] = now
    return state


def main():
    cred = credentials.Certificate(json.loads(SERVICE_ACCOUNT_JSON))
    firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

    state = load_state()
    state = check_events(state)
    state = check_codes(state)
    state = maybe_post_leaderboard(state)
    save_state(state)

    print("Готово: состояние проверено и сохранено.")


if __name__ == "__main__":
    main()
