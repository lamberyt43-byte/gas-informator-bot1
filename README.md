# Grow And Sell — бот-информатор

Бот слушает Firebase Realtime Database игры и постит в Telegram-канал:

- начало/конец ивентов (Диско, Дождь удачи и т.д.)
- новые промокоды (`dynamicCodes`)
- таблицу лидеров раз в N часов

Работает **без сервера**: GitHub Actions запускает скрипт по расписанию
(по умолчанию каждые 10 минут), скрипт сверяет текущее состояние базы с
тем, что сохранено в самой Firebase (`announcerBotState`), постит
изменения и завершает работу. Держать компьютер или телефон включённым
не нужно.

## Файлы

- `announcer_bot.py` — сам бот (разовый запуск, не постоянный процесс)
- `.github/workflows/announcer.yml` — расписание запуска в GitHub Actions

## Установка

1. Репозиторий должен быть **публичным** — тогда минуты Actions
   бесплатны и не ограничены. В коде и файлах репозитория нет ничего
   секретного, все ключи хранятся в GitHub Secrets.
2. Firebase Console → Project Settings → Service accounts →
   **Generate new private key** → скачать JSON.
3. Repository → Settings → Secrets and variables → Actions →
   **New repository secret**, добавить:

   | Secret | Значение |
   |---|---|
   | `BOT_TOKEN` | токен бота от @BotFather |
   | `CHANNEL_ID` | `@your_channel` или числовой chat_id |
   | `DATABASE_URL` | `https://<project-id>-default-rtdb...` |
   | `FIREBASE_SERVICE_ACCOUNT_JSON` | весь JSON из шага 2, целиком |

4. Добавить бота **администратором** в канал в Telegram.
5. Actions → workflow **Announcer bot** → **Run workflow** — проверить
   вручную, что всё работает, не дожидаясь расписания.

## Настройка (необязательно)

Переменные окружения `LEADERBOARD_INTERVAL_HOURS` (по умолчанию `24`) и
`LEADERBOARD_TOP_N` (по умолчанию `10`) можно задать как обычные
Repository variables (Settings → Secrets and variables → Actions →
Variables) — тогда не придётся трогать код.

Чтобы бот реагировал быстрее, поменяйте `cron: '*/10 * * * *'` в
`announcer.yml` на, например, `'*/5 * * * *'` (каждые 5 минут).

## Безопасность

- Никогда не коммитьте `serviceAccountKey.json` в репозиторий — только
  через Secrets.
- Если ключ где-то засветился (например, случайно попал в чат или в
  публичный файл) — сразу отзовите его в Firebase Console и
  сгенерируйте новый.
