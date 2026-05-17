# nano-bot

A personal assistant Telegram bot. Built on [aiogram](https://docs.aiogram.dev/) v3, managed with [uv](https://docs.astral.sh/uv/).

## Features

- **Morning briefing** — sends a weather + market report at a set local time.
- **Weather** (`/weather`) — today's conditions from [Open-Meteo](https://open-meteo.com/): icon, location, temp range, precipitation, and wind.
- **Stocks** (`/stocks`) — per-ticker price with 1-day, 1-week, and 1-month change from Yahoo Finance.

## Layout

```
src/nano_bot/
  app.py           # entry point: wires bot, dispatcher, scheduler
  config.py        # Settings dataclass + .env loader
  handlers.py      # aiogram Router (/start, /help, /weather, /stocks, echo)
  reports.py       # composes morning report from services
  scheduler.py     # APScheduler cron job for the morning broadcast
  services/
    weather.py     # Open-Meteo client
    stocks.py     # yfinance client
```

## Setup

```bash
uv sync
cp .env.example .env
# edit .env: BOT_TOKEN, CHAT_IDS, LATITUDE/LONGITUDE, LOCATION_NAME, TIMEZONE, NOTIFY_TIME, TICKERS
```

Message the bot with `/start` to discover your chat id.

## Secrets & local env

- `.env` is gitignored; `.env.example` is tracked. **Never put real values in `.env.example`.**
- `BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `CHAT_IDS` are the only true secrets; everything else has sensible defaults.
- If a token ever leaks, revoke it in @BotFather via `/revoke` and issue a new one.
- A `pre-commit` config with [gitleaks](https://github.com/gitleaks/gitleaks) blocks accidental commits of high-entropy strings (Telegram tokens, API keys, private keys). Install once per clone:

  ```bash
  uv tool install pre-commit
  pre-commit install
  ```

  Run on demand against the whole repo: `pre-commit run --all-files`.

## Run

```bash
uv run nano-bot
# or
uv run python -m nano_bot
```

## Deploy with Docker

A multi-stage `Dockerfile` builds a slim runtime image; `docker-compose.yml` wires it up with `.env` and persistent restart.

```bash
# Build the image and start in the background.
docker compose up -d --build

# Tail the logs.
docker compose logs -f

# Stop and remove the container.
docker compose down
```

The container runs as a non-root user, reads secrets from `.env` (never baked into the image), and reconnects automatically on failure (`restart: unless-stopped`). No ports are exposed — the bot uses Telegram long polling for outbound-only connectivity.

## Commands

- `/start` — greet and show chat id
- `/help` — list commands
- `/weather` — today's forecast (with location, temp range, precip, wind)
- `/stocks` — latest quote per ticker, with 1d / 1w / 1m % change

## Configuration

All values are read from `.env` (see `.env.example`).

| Variable | Required | Purpose |
|---|---|---|
| `BOT_TOKEN` | yes | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `CHAT_IDS` | yes | Comma-separated chat ids for the morning broadcast |
| `LATITUDE` / `LONGITUDE` | yes | Coordinates for the weather forecast |
| `LOCATION_NAME` | no | Display name shown in the weather header (e.g. `Bellevue, WA`) |
| `TIMEZONE` | no | IANA tz for the cron job (default `UTC`) |
| `NOTIFY_TIME` | no | `HH:MM` local time for the morning broadcast (default `08:00`) |
| `TICKERS` | no | Comma-separated Yahoo Finance symbols; leave empty to disable the market section |

## Morning notification

An APScheduler cron job fires every day at `NOTIFY_TIME` (local `TIMEZONE`, default `08:00`) and broadcasts a combined report to every chat id in `CHAT_IDS`:

- Weather forecast from [Open-Meteo](https://open-meteo.com/) (no API key).
- Per-ticker quotes from Yahoo Finance (`yfinance`) showing price, 1-day, 1-week (~5 trading days), and 1-month (~21 trading days) percentage change. Leave `TICKERS` empty to disable the market section.
