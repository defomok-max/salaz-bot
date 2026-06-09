# Instagram Dork Search Bot

Telegram bot that finds Instagram posts selling something (furniture, phones,
laptops, anything) and that publish an **email and at least one phone**
(WhatsApp link _or_ a regular phone number). Posts originating from CIS
countries (RU/BY/KZ/UA/UZ/AM/AZ/GE/MD/KG/TJ/TM) are filtered out.

## Deploy to Render

The repo is Render-ready: `render.yaml` declares the service, `requirements.txt`
pins Python deps, and the in-process aiohttp `/health` endpoint is what Render
probes for liveness.

1. Push to GitHub.
2. Open https://dashboard.render.com/blueprints → **New Blueprint Instance** → pick the repo.
3. Set `TELEGRAM_BOT_TOKEN` and `SERPER_API_KEY` in the Environment tab.
4. Wait for the first deploy. The bot is up when the log shows `starting bot polling`.

Persistent disk for the SQLite history is **only on paid plans**. Free Web
Services work, but `history.db` resets on every deploy. See **[RENDER.md](RENDER.md)**
for the full guide (Docker, env vars, troubleshooting).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/YOUR_USER/salazpre)

## How it works

1. The user sends a keyword to the Telegram bot (e.g. `iphone 14`).
2. The bot builds several Google dorks (`site:instagram.com "<keyword>" "wa.me" "@gmail.com" ...`).
3. Each dork is executed via [Serper.dev](https://serper.dev) (a Google
   search API).
4. For every hit the snippet is parsed: an email regex extracts emails;
   `wa.me` / `whatsapp` patterns extract WhatsApp numbers; remaining
   phone-shaped strings are extracted as plain phone numbers (all
   normalised via [phonenumbers](https://pypi.org/project/phonenumbers/)).
5. Results without an email, or without **any** phone signal, or that look
   CIS-origin (Cyrillic text / CIS country names / CIS phone country
   codes), are dropped.
6. Up to 50 unique Instagram URLs are returned in a single message that is
   edited in place — paginate with the inline ◀ / ▶ buttons.
7. Every completed search is saved to a local SQLite history; the
   **📜 История** button recalls past results.

## UX

The bot keeps a single "control message" per chat and edits it on every
step (menu → keyword prompt → progress → results → history). There is no
persistent reply keyboard; navigation is entirely inline.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/rustamanjies-arch/Salaz.git
cd Salaz
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# edit .env and set TELEGRAM_BOT_TOKEN and SERPER_API_KEY
```

### Required environment variables

| Var | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather). |
| `SERPER_API_KEY` | API key from [serper.dev](https://serper.dev). |

### Optional

| Var | Default | Description |
| --- | --- | --- |
| `ALLOWED_USER_IDS` | (empty = open) | CSV of Telegram user IDs allowed to use the bot. |
| `MAX_RESULTS` | `50` | Cap for results returned per search. |
| `RESULTS_PER_QUERY` | `10` | Page size requested from Serper.dev. The free tier rejects values above ~10 with HTTP 400; raise on paid plans. |
| `MAX_SEARCH_CALLS` | `15` | Hard cap on Google calls per user query (cost guard). |
| `SEARCH_TIMEOUT_SECONDS` | `20` | HTTP timeout for the search backend. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. |
| `HISTORY_DB_PATH` | `history.db` | Path to the SQLite database that stores search history. |
| `HISTORY_PER_PAGE` | `10` | Number of past searches per page in the history list. |
| `RESULTS_PER_PAGE` | `10` | Number of listings per results page. |

## Run

```bash
python -m instagram_dork_bot
# or
instagram-dork-bot
```

Then open Telegram, find your bot, send `/start`, then `/search iphone 14`
or just `iphone 14` as a plain message.

## Dev

```bash
ruff check .
pytest
```

## Limitations

- Results are limited to whatever Google has indexed on `instagram.com`.
  Instagram aggressively rate-limits direct scraping, but search-engine
  indexes give us a wide enough net for lead generation.
- The "no CIS" rule is a heuristic (Cyrillic + country names + phone
  country codes). It is conservative, so a few false positives may be
  filtered out.
- Acceptance requires email + at least one phone (WhatsApp link, "WhatsApp"
  inline, or a plain phone number). Posts with neither contact channel are
  dropped.
- Use responsibly. Respect Instagram's terms and applicable privacy law
  (e.g. GDPR / CCPA) when contacting people you find via this tool.
