# Деплой на Render (https://render.com)

Этот бот работает на Render как **Web Service** (есть встроенный health-сервер на `aiohttp`).

## 1. Подготовка репозитория

Файлы для деплоя уже лежат в корне:

| Файл | Назначение |
| --- | --- |
| `render.yaml` | Blueprint — Render подхватит его автоматически |
| `requirements.txt` | список зависимостей для `pip install` |
| `runtime.txt` | фиксирует Python 3.11.10 |
| `Dockerfile` | (опционально) если хочешь собрать через Docker |
| `.dockerignore` | исключает тесты/кэши из образа |
| `Procfile` | (опционально) запасной вариант для `worker`-dyno |

> ⚠️ **Не коммить `.env`!** Токены и ключи задаются в UI Render → `Environment`.

## 2. Создай новый сервис на Render

Есть два пути:

### Способ A: Blueprint (рекомендую)

1. Залей репозиторий на GitHub.
2. Открой https://dashboard.render.com/blueprints.
3. **New Blueprint Instance** → выбери репо → **Apply**.
4. Render прочитает `render.yaml` и сам создаст Web Service.

### Способ B: вручную через UI

1. https://dashboard.render.com → **New +** → **Web Service**.
2. Подключи GitHub-репо.
3. Заполни:
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command**: `python -m instagram_dork_bot`
   - **Health Check Path**: `/health`
   - **Plan**: минимум `Starter` ($7/мес) — нужно для persistent disk
4. **Advanced → Disk**: добавь диск `history-db`, mount path `/var/data`, 1 GB.

## 3. Переменные окружения

В **Environment** сервиса задай (минимум):

| Key | Value | Sync? |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather | ❌ (только ты) |
| `SERPER_API_KEY` | ключ от https://serper.dev | ❌ |
| `ADMIN_USER_IDS` | свой Telegram ID (можно оставить пустым) | ❌ |
| `ALLOWED_USER_IDS` | свой Telegram ID (чтобы пройти гейт) | ❌ |
| `HISTORY_DB_PATH` | `/var/data/history.db` | ✅ (уже в yaml) |
| `PORT` | `10000` | ✅ (Render сам ставит) |
| `LOG_LEVEL` | `INFO` | ✅ |

> **Sync: false** — Render **не** сохраняет значение в git, только в своём хранилище.

Где взять свой Telegram ID: напиши `@userinfobot` в Telegram — он пришлёт число.

## 4. Запусти деплой

Жми **Deploy**. Логи будут в `Logs` вкладке. Бот стартует за ~30-60 сек.

Проверка:
- В логах: `health-check server listening on :10000` + `starting bot polling`.
- На `https://<service-name>.onrender.com/health` — должен ответить `ok`.

## 5. Бесплатный план (без persistent disk)

Render Free Web Services **не поддерживают persistent disk** — SQLite-база будет
обнуляться при каждом редеплое. Если тебе не нужна история поисков между
перезапусками — оставь `HISTORY_DB_PATH` пустым (используется дефолт
`history.db` в эфемерной ФС), и просто не обращай внимания на потерю истории
после редеплоя.

Если история нужна — обновись до платного плана (от $7/мес за Web Service +
$0.15/мес за 1 GB диск) **или** подключи внешнюю БД (Supabase Postgres /
Render Postgres) — для этого нужно переписать `storage.py` с `aiosqlite` на
`asyncpg`. Это уже отдельная задача.

## 6. Локальная проверка через Docker

```bash
docker build -t salaz-bot .
docker run --rm -p 10000:10000 \
  -e TELEGRAM_BOT_TOKEN=... \
  -e SERPER_API_KEY=... \
  -v $PWD/data:/var/data \
  salaz-bot
```

В другом терминале:
```bash
curl http://localhost:10000/health
# → ok
```

## 7. Частые проблемы

| Симптом | Причина | Фикс |
| --- | --- | --- |
| Бот упал при старте: `No Serper API keys configured` | Не задан `SERPER_API_KEY` | Добавь в Environment |
| Бот упал: `TELEGRAM_BOT_TOKEN is required` | Пустой токен | Проверь значение (нет лишних пробелов/кавычек) |
| Render показывает unhealthy | Health-сервер не успел стартовать | Подожди 60 сек, в логах должен быть `health-check server listening` |
| `sqlite3.OperationalError: unable to open database file` | Не создан `/var/data` | В `Dockerfile` уже создаётся; в native runtime — создаётся автоматически через `storage.py:parent.mkdir(parents=True, exist_ok=True)` |
| Сервис «спит» и не отвечает | Free Web Service засыпает после 15 мин без запросов | Перейди на платный план или используй cron-pinger (UptimeRobot → `/health`) |

## 8. Чек-лист перед прод-деплоем

- [ ] Токен бота отозван в `@BotFather` (если был утёк)
- [ ] Serper-ключ отозван и заменён
- [ ] `ALLOWED_USER_IDS` содержит твой ID
- [ ] (опц.) `ADMIN_USER_IDS` содержит твой ID — иначе первый запуск
      автоматически промоутит первого юзера, который нажмёт `/start`
- [ ] В логах нет `WARNING: key_suffix=` с `****` (это нормально) и
      нет полных токенов
- [ ] Тесты проходят локально: `pytest -q` (165 passed)
- [ ] `ruff check .` чистый
