# AGENTS.md

Personal-assistant Telegram bot (aiogram v3, async). Long-polling only; no inbound ports.

## Commands

- `uv sync` — install deps (managed by uv; **requires Python >=3.14**).
- `uv run nano-bot` — run the bot (equivalently `uv run python -m nano_bot`).
- `uv run ruff check .` / `uv run ruff format .` — lint/format. Ruff is the
  formatter/linter but is **not** configured in `pyproject.toml` and **not**
  enforced in CI; run it manually before committing.

There is **no test suite**. CI (`.github/workflows/ci.yml`) only builds the
Docker image on PRs to `main`; it does not run lint, typecheck, or tests.

## Layout

`src/nano_bot/` (package boundary set in `pyproject.toml` hatch wheel config):

- `app.py` — entry point; wires bot, dispatcher, scheduler. Registers Telegram
  slash-command menu in `BOT_COMMANDS` — keep it in sync with `handlers.py`.
- `config.py` — frozen `Settings` dataclass + `load_settings()` (.env parser).
- `handlers.py` — aiogram router: `/start`, `/help`, `/weather [city]`,
  `/stock [TICKER ...]`, `/anime [week]`, `/douban [movies|books]`, `/cashback`,
  `/flight`.
- `reports.py` — composes the morning broadcast, the cashback report and the
  `/flight` report from services.
- `scheduler.py` — APScheduler cron for the daily broadcast, the quarterly
  cashback heads-up (15th of Mar/Jun/Sep/Dec) and Alaska promotion checks
  (every 2 hours, 07:00–21:00; alert state is in memory only).
- `services/weather.py` (Open-Meteo), `services/stock.py` (yfinance),
  `services/anilist.py` (AniList), `services/douban.py` (Douban),
  `services/cashback.py` (Chase Freedom Flex page + Discover calendar JSON;
  both undocumented, parsed best-effort),
  `services/alaska.py` (Alaska fare sale + Global Getaways pages; parsed
  best-effort, unknown layouts raise `UnrecognizedPageError`),
  `services/_retry.py` (shared sync/async exponential-backoff retry helpers —
  reuse these for new external calls rather than rolling your own).

## Gotchas

- `.env` is gitignored; `.env.example` is tracked. **Never put real values in
  `.env.example`.** Only `BOT_TOKEN` and `CHAT_IDS` are true secrets.
- `load_settings()` raises on missing `BOT_TOKEN`/`CHAT_IDS`; other vars have
  defaults (see `config.py`).
- Deploy is automatic on push to `main` (`.github/workflows/deploy.yml`):
  builds + pushes to GHCR, then SSHes to an Azure VM and restarts the container.
  Prod `.env` lives in the `ENV_FILE` GitHub environment secret, not the repo.
