"""Stock quote helpers backed by yfinance (Yahoo Finance)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import yfinance as yf

CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "JPY": "\u00a5",
    "CNY": "\u00a5",
    "KRW": "\u20a9",
    "INR": "\u20b9",
}


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float
    change_pct: float
    change_pct_week: float | None
    change_pct_month: float | None
    currency: str

    def format(self) -> str:
        if self.change_pct > 0:
            arrow = "\U0001f4c8"  # 📈
        elif self.change_pct < 0:
            arrow = "\U0001f4c9"  # 📉
        else:
            arrow = "\u27a1\ufe0f"  # ➡️
        symbol = CURRENCY_SYMBOLS.get(self.currency.upper())
        price = (
            f"{symbol}{self.price:,.2f}"
            if symbol
            else f"{self.price:,.2f} {self.currency}"
        )

        def _fmt(pct: float | None) -> str:
            if pct is None:
                return "n/a"
            sign = "+" if pct > 0 else ""
            return f"{sign}{pct:.2f}%"

        return (
            f"{arrow} <b>{self.symbol}</b>  {price}  ({_fmt(self.change_pct)})\n"
            f"     1w: {_fmt(self.change_pct_week)}  \u00b7  1m: {_fmt(self.change_pct_month)}"
        )


def _pct_change_over(closes, bars_back: int) -> float | None:
    """Return % change between the last close and the close `bars_back` bars earlier."""
    if len(closes) <= bars_back:
        return None
    last = float(closes.iloc[-1])
    past = float(closes.iloc[-1 - bars_back])
    if not past:
        return None
    return (last - past) / past * 100


def _fetch_one(symbol: str) -> Quote | None:
    ticker = yf.Ticker(symbol)
    # 3mo of daily bars: enough for daily, ~5-bar week, ~21-bar month windows.
    hist = ticker.history(period="3mo", interval="1d", auto_adjust=False)
    if hist.empty:
        return None
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    change_pct = (last - prev) / prev * 100 if prev else 0.0
    change_pct_week = _pct_change_over(closes, 5)
    change_pct_month = _pct_change_over(closes, 21)
    currency = (ticker.fast_info.get("currency") if hasattr(ticker, "fast_info") else None) or "USD"
    return Quote(
        symbol=symbol.upper(),
        price=last,
        change_pct=change_pct,
        change_pct_week=change_pct_week,
        change_pct_month=change_pct_month,
        currency=currency,
    )


async def fetch_quotes(symbols: list[str]) -> list[Quote]:
    """Fetch quotes off the event loop (yfinance is sync)."""

    def _gather() -> list[Quote]:
        out: list[Quote] = []
        for sym in symbols:
            try:
                q = _fetch_one(sym)
            except Exception:
                q = None
            if q is not None:
                out.append(q)
        return out

    return await asyncio.to_thread(_gather)


def format_report(quotes: list[Quote]) -> str:
    if not quotes:
        return "<b>Market</b>\nNo quotes available."
    lines = ["<b>Market</b>"]
    lines.extend(q.format() for q in quotes)
    return "\n".join(lines)
