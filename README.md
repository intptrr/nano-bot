# Nano Bot

A personal assistant Telegram bot. Built on [aiogram](https://docs.aiogram.dev/) v3, managed with [uv](https://docs.astral.sh/uv/).

## Features

- **Morning briefing** — sends a weather + market report at a set local time.
- **Weather** (`/weather [city]`) — today's conditions from [Open-Meteo](https://open-meteo.com/): icon, location, temp range, precipitation, and wind.
- **Stocks** (`/stock [TICKER ...]`) — per-ticker price with 1-day, 1-week, and 1-month change from Yahoo Finance.
- **Anime** (`/anime [week]`) — highly-rated anime airing today (or this week, grouped by day) from [AniList](https://anilist.co/), ranked by a popularity- and favourites-weighted score.

## Layout

```
src/nano_bot/
  app.py           # entry point: wires bot, dispatcher, scheduler
  config.py        # Settings dataclass + .env loader
  handlers.py      # aiogram Router (/start, /help, /weather, /stock, /anime)
  reports.py       # composes morning report from services
  scheduler.py     # APScheduler cron job for the morning broadcast
  services/
    weather.py     # Open-Meteo client (geocoding + forecast)
    stock.py       # yfinance client
    anilist.py     # AniList client (airing schedule + weighted rating)
    _retry.py      # shared sync/async exponential-backoff retry helpers
```

## Setup

```bash
uv sync
cp .env.example .env
# edit .env: BOT_TOKEN, CHAT_IDS, LOCATION_NAME, TIMEZONE, NOTIFY_TIME, TICKERS
```

Message the bot with `/start` to discover your chat id.

## Secrets & local env

- `.env` is gitignored; `.env.example` is tracked. **Never put real values in `.env.example`.**
- `BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `CHAT_IDS` are the only true secrets; everything else has sensible defaults.
- If a token ever leaks, revoke it in @BotFather via `/revoke` and issue a new one.

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

## Continuous deployment (Azure VM)

`.github/workflows/deploy.yml` builds a Docker image on every push to `main`, pushes it to GitHub Container Registry (GHCR), then SSHes into an Azure VM to sync the `.env` file and restart the container.

### One-time VM setup

```bash
# Install Docker.
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out/in after
```

The workflow creates `~/apps/nano-bot/` automatically. Outbound 443 is the only network requirement (Telegram long polling).

### GitHub configuration

Create a `prod` environment (Settings → Environments → New environment) and add the following **environment secrets**:

| Secret | Value |
|---|---|
| `AZURE_VM_HOST` | VM public IP or DNS |
| `AZURE_VM_USER` | SSH user (e.g. `azureuser`) |
| `AZURE_VM_SSH_KEY` | Private SSH key authorized on the VM |
| `ENV_FILE` | Full contents of the production `.env` |

`GITHUB_TOKEN` is injected automatically and is used by the VM to pull the private GHCR image.

### Flow

1. Push to `main` → image built and pushed as `ghcr.io/<owner>/nano-bot:latest` (and `:<sha>`).
2. Deploy job (gated on the `prod` environment) writes `~/apps/nano-bot/.env` from `ENV_FILE`.
3. The VM logs in to GHCR with the workflow token, pulls the new image, and replaces the running container.

To rotate any value in `.env`, update the `ENV_FILE` secret and re-run the workflow — no SSH session required.

## Commands

- `/start` — greet and show chat id
- `/help` — list commands
- `/weather [city]` — today's forecast for the configured city, or a city passed as an argument (location, temp range, precip, wind)
- `/stock [TICKER ...]` — latest quote per ticker, with 1d / 1w / 1m % change; uses configured `TICKERS` or symbols passed as arguments
- `/anime [week]` — highly-rated anime airing today; pass `week` to list the next 7 days grouped by day. Sourced from [AniList](https://anilist.co/); each title shows a popularity- and favourites-weighted score

## Configuration

All values are read from `.env` (see `.env.example`).

| Variable | Required | Purpose |
|---|---|---|
| `BOT_TOKEN` | yes | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `CHAT_IDS` | yes | Comma-separated chat ids for the morning broadcast |
| `LOCATION_NAME` | yes | City name geocoded via Open-Meteo (e.g. `Bellevue, WA`) |
| `TIMEZONE` | no | IANA tz for the cron job (default `UTC`) |
| `NOTIFY_TIME` | no | `HH:MM` local time for the morning broadcast (default `08:00`) |
| `TICKERS` | no | Comma-separated Yahoo Finance symbols; leave empty to disable the market section |

## Morning notification

An APScheduler cron job fires every day at `NOTIFY_TIME` (local `TIMEZONE`, default `08:00`) and broadcasts a combined report to every chat id in `CHAT_IDS`:

- Weather forecast from [Open-Meteo](https://open-meteo.com/) (no API key).
- Per-ticker quotes from Yahoo Finance (`yfinance`) showing price, 1-day, 1-week (~5 trading days), and 1-month (~21 trading days) percentage change. Leave `TICKERS` empty to disable the market section; it is also skipped on weekends.

If a section fails, it is replaced with a short placeholder so the broadcast still goes out.
