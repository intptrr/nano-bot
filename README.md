# Nano Bot

A personal assistant Telegram bot built on [aiogram](https://docs.aiogram.dev/) v3 and managed with [uv](https://docs.astral.sh/uv/).

## Commands

| Command | What it does |
|---|---|
| `/weather [city]` | Today's forecast for `LOCATION_NAME` or a given city ([Open-Meteo](https://open-meteo.com/)) |
| `/stock [TICKER ...]` | Price with 1-day, 1-week and 1-month change for `TICKERS` or the given symbols |
| `/anime [week\|season]` | Highly rated anime airing today, this week or new this season, with Chinese names ([Bangumi](https://bgm.tv/)) |
| `/douban [movies\|books]` | Trending [Douban](https://m.douban.com/) movies and books |
| `/cashback` | Chase Freedom Flex and Discover it 5% categories for this and next quarter |
| `/flight` | Current Alaska Airlines fare sale and Global Getaways |
| `/start`, `/help` | Show your chat id; list commands |

## Notifications

Sent to every chat in `CHAT_IDS`, at `NOTIFY_TIME` in `TIMEZONE` unless noted:

| When | What |
|---|---|
| Daily | Weather and market report (market skipped on weekends) |
| 15th of Mar/Jun/Sep/Dec | Next quarter's Chase and Discover 5% categories |
| 21st of Jan/Apr/Jul/Oct | The new season's highly rated anime |
| Every 2 hours, 07:00–21:00 | Alert when an Alaska fare sale or Global Getaways starts |

## Layout

```
src/nano_bot/
  app.py           # entry point: wires bot, dispatcher, scheduler
  config.py        # Settings dataclass + .env loader
  handlers.py      # command handlers
  reports.py       # composes messages from services
  scheduler.py     # scheduled notifications
  services/
    weather.py     # Open-Meteo forecast
    stock.py       # Yahoo Finance quotes
    bangumi.py     # Bangumi anime calendar + seasons
    douban.py      # Douban trending movies + books
    cashback.py    # Chase + Discover 5% calendars
    alaska.py      # Alaska fare sales + Global Getaways
    _retry.py      # shared retry helpers
```

## Setup

```bash
uv sync
cp .env.example .env   # fill in your values; never put real values in .env.example
```

| Variable | Required | Purpose |
|---|---|---|
| `BOT_TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `CHAT_IDS` | yes | Comma-separated chat ids to notify (send `/start` to find yours) |
| `LOCATION_NAME` | yes | City for the forecast, e.g. `Bellevue, WA` |
| `TIMEZONE` | no | IANA time zone (default `UTC`) |
| `NOTIFY_TIME` | no | `HH:MM` for the scheduled notifications (default `08:00`) |
| `TICKERS` | no | Comma-separated Yahoo Finance symbols; leave empty to skip the market report |

## Run

```bash
uv run nano-bot
```

Or in Docker: `docker compose up -d --build`.

## Deployment

Every push to `main` builds an image, pushes it to GHCR and restarts the container on an Azure VM over SSH (`.github/workflows/deploy.yml`). The VM needs Docker, with the SSH user in the `docker` group.

Secrets in the GitHub `prod` environment:

| Secret | Value |
|---|---|
| `AZURE_VM_HOST` | VM public IP or DNS |
| `AZURE_VM_USER` | SSH user |
| `AZURE_VM_SSH_KEY` | Private SSH key for that user |
| `ENV_FILE` | Contents of the production `.env` |

To change a setting in production, update `ENV_FILE` and re-run the workflow.
