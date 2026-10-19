# Nano Bot

A personal assistant Telegram bot. Built on [aiogram](https://docs.aiogram.dev/) v3, managed with [uv](https://docs.astral.sh/uv/).

## Features

- **Morning briefing** — sends a weather + market report at a set local time.
- **Weather** (`/weather [city]`) — today's conditions from [Open-Meteo](https://open-meteo.com/): icon, location, temp range, precipitation, and wind.
- **Stocks** (`/stock [TICKER ...]`) — per-ticker price with 1-day, 1-week, and 1-month change from Yahoo Finance.
- **Anime** (`/anime [week|season]`) — highly rated anime airing today, this week (grouped by day) or new this season, with Chinese names from [Bangumi](https://bgm.tv/). A notification lists the new season's best on the 21st of Jan/Apr/Jul/Oct.
- **Douban** (`/douban [movies|books]`) — trending Chinese-community picks from [Douban](https://m.douban.com/): now-showing movies and this month's hot books, each with its 10-point rating and rating count.
- **Cash back** (`/cashback`) — Chase Freedom Flex and Discover it 5% rotating categories for this and next quarter. A heads-up with next quarter's categories goes out on the 15th of Mar/Jun/Sep/Dec.
- **Alaska promotions** (`/flight`) — an alert when Alaska Airlines starts a fare sale or an Atmos Rewards Global Getaways award sale, and a command to check both anytime.

## Layout

```
src/nano_bot/
  app.py           # entry point: wires bot, dispatcher, scheduler
  config.py        # Settings dataclass + .env loader
  handlers.py      # aiogram Router (/start, /help, /weather, /stock, /anime, /douban, /cashback, /flight)
  reports.py       # composes morning, cash back, flight and anime reports from services
  scheduler.py     # APScheduler cron jobs (morning broadcast, cash back heads-up, Alaska alerts, anime season)
  services/
    weather.py     # Open-Meteo client (geocoding + forecast)
    stock.py       # yfinance client
    bangumi.py     # Bangumi client (weekly calendar + seasons, vote-weighted rating)
    douban.py      # Douban client (trending movies + monthly books)
    cashback.py    # Chase + Discover 5% category calendars
    alaska.py      # Alaska fare sale + Global Getaways pages
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
- `/anime [week|season]` — highly rated anime airing today; pass `week` for the next 7 days grouped by day, or `season` for this season's new TV anime. Sourced from [Bangumi](https://bgm.tv/) with Chinese names; each title shows its Bangumi score and vote count. Only shows scoring 6.5+ once their score is weighted by votes are listed, so brand-new shows appear after people rate them. Days follow Japan's broadcast day
- `/douban [movies|books]` — trending [Douban](https://m.douban.com/) now-showing movies and this month's hot books with their 10-point rating and rating count; pass `movies` or `books` to show just one section (content is zh-CN)
- `/cashback` — Chase Freedom Flex and Discover it 5% categories for this and next quarter
- `/flight` — whether an Alaska fare sale or Global Getaways is running right now, with links to both pages

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

## Cash back heads-up

On the 15th of March, June, September and December at `NOTIFY_TIME` (local `TIMEZONE`), a separate message with next quarter's 5% categories goes to every chat id in `CHAT_IDS`. Chase opens activation that day and Discover on the 1st of the month; Discover only earns 5% after you activate, so the message lands before the quarter starts.

- Chase Freedom Flex categories come from the calendar on the [Freedom Flex page](https://creditcards.chase.com/cash-back-credit-cards/freedom/flex).
- Discover it categories come from the JSON feed behind its [5% calendar](https://www.discover.com/credit-cards/cash-back/cashback-calendar.html).

Neither source is a documented API. If one fails or changes shape, that card shows a link instead and the error is logged.

## Alaska promotions

Every 2 hours from 07:00 to 21:00 (local `TIMEZONE`), the bot checks two of Alaska Airlines' own pages and sends a separate alert to every chat id in `CHAT_IDS` when something new starts:

- **Fare sales** from the [sale page](https://www.alaskaair.com/content/deals/flights/sale): the sale's description, end date and a link to the page.
- **Global Getaways** from the [Global Getaways page](https://www.alaskaair.com/content/global-getaways): discounted award flights released on Atmos Members Day (usually the first Wednesday of the month), with the book-by date, travel window, each destination's starting points and a link to the page.

Each promotion is announced once; the next one alerts again after it ends. What has been sent is only kept in memory, so a promotion still running after a restart or deploy is announced again. If a page loads but no longer matches its known layouts, a one-time "page changed" alert links to it instead. `/flight` shows the current state of both pages on demand without affecting the alerts.

## Anime season notification

On the 21st of January, April, July and October at `NOTIFY_TIME` (local `TIMEZONE`), a separate message to every chat id in `CHAT_IDS` lists the season's highly rated new TV anime from [Bangumi](https://bgm.tv/) — the same list as `/anime season`. It goes out about three weeks into the season so new shows have collected enough votes to be rated; if none qualify yet, the message says so.
