"""Binance USDⓈ-M futures public REST data (no API key needed)."""

from datetime import datetime, timezone

from .http import client

BASE = "https://fapi.binance.com"

# Stablecoin / fiat bases excluded from the "top 20 by volume" universe.
STABLE_BASES = {"USDC", "USDT", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDE", "USD1", "EUR", "PYUSD", "RLUSD"}
QUOTE_PREFERENCE = ("USDT", "USDC")
MULTIPLIER_PREFIXES = ("", "1000", "1000000", "1M")


def fetch_market(watch_bases: list[str]) -> dict:
    """Everything the brief needs from Binance, in one pass."""
    with client() as c:
        info = c.get(f"{BASE}/fapi/v1/exchangeInfo").raise_for_status().json()
        tickers = c.get(f"{BASE}/fapi/v1/ticker/24hr").raise_for_status().json()
        premium = c.get(f"{BASE}/fapi/v1/premiumIndex").raise_for_status().json()

        perps = tradable_perps(info)
        ticker_by = {t["symbol"]: t for t in tickers}
        funding_by = {p["symbol"]: float(p.get("lastFundingRate") or 0) for p in premium}

        top = top_by_volume(perps, ticker_by, n=20)
        resolved, missing = resolve_bases(watch_bases, perps)

        majors = {}
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            if sym not in ticker_by:
                continue
            oi = c.get(f"{BASE}/fapi/v1/openInterest", params={"symbol": sym}).raise_for_status().json()
            klines = c.get(f"{BASE}/fapi/v1/klines", params={"symbol": sym, "interval": "1d", "limit": 2}).raise_for_status().json()
            row = summarize(sym, ticker_by[sym], funding_by)
            row["open_interest_usd"] = float(oi["openInterest"]) * row["price"]
            row["yesterday_candle"] = last_closed_candle(klines)
            majors[sym] = row

    return {
        "majors": majors,
        "top20": [summarize(s, ticker_by[s], funding_by) for s in top],
        "watchlist": [summarize(s, ticker_by[s], funding_by) for s in resolved.values() if s in ticker_by],
        "watchlist_missing": missing,
        "funding_extremes": funding_extremes(set(top) | set(resolved.values()), funding_by),
    }


def universe(watch_bases: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(top-20 symbols, resolved watchlist symbols, unresolved watchlist names)."""
    with client() as c:
        info = c.get(f"{BASE}/fapi/v1/exchangeInfo").raise_for_status().json()
        tickers = c.get(f"{BASE}/fapi/v1/ticker/24hr").raise_for_status().json()
    perps = tradable_perps(info)
    top = top_by_volume(perps, {t["symbol"]: t for t in tickers}, n=20)
    resolved, missing = resolve_bases(watch_bases, perps)
    return top, list(resolved.values()), missing


def all_prices() -> dict[str, float]:
    with client(timeout=10) as c:
        rows = c.get(f"{BASE}/fapi/v1/ticker/price").raise_for_status().json()
    return {r["symbol"]: float(r["price"]) for r in rows}


def open_interest(symbol: str) -> float:
    """Open interest in contracts (base units)."""
    with client(timeout=10) as c:
        oi = c.get(f"{BASE}/fapi/v1/openInterest", params={"symbol": symbol}).raise_for_status().json()
    return float(oi["openInterest"])


def tradable_perps(exchange_info: dict) -> dict[str, dict]:
    """symbol -> exchangeInfo row, for perpetual contracts currently trading."""
    return {
        s["symbol"]: s
        for s in exchange_info.get("symbols", [])
        if "PERPETUAL" in s.get("contractType", "") and s.get("status") == "TRADING"
    }


def top_by_volume(perps: dict[str, dict], ticker_by: dict[str, dict], n: int = 20) -> list[str]:
    rows = [
        (float(ticker_by[sym]["quoteVolume"]), sym)
        for sym, meta in perps.items()
        if sym in ticker_by
        and meta.get("quoteAsset") == "USDT"
        and meta.get("baseAsset") not in STABLE_BASES
    ]
    return [sym for _, sym in sorted(rows, reverse=True)[:n]]


def resolve_bases(bases: list[str], perps: dict[str, dict]) -> tuple[dict[str, str], list[str]]:
    """Map watchlist names (e.g. "PEPE") to perp symbols (e.g. "1000PEPEUSDT")."""
    by_base_quote = {(m.get("baseAsset"), m.get("quoteAsset")): sym for sym, m in perps.items()}
    resolved, missing = {}, []
    for base in bases:
        sym = next(
            (
                by_base_quote[(prefix + base, quote)]
                for quote in QUOTE_PREFERENCE
                for prefix in MULTIPLIER_PREFIXES
                if (prefix + base, quote) in by_base_quote
            ),
            None,
        )
        if sym:
            resolved[base] = sym
        else:
            missing.append(base)
    return resolved, missing


def summarize(symbol: str, ticker: dict, funding_by: dict[str, float]) -> dict:
    return {
        "symbol": symbol,
        "price": float(ticker["lastPrice"]),
        "change_24h_pct": float(ticker["priceChangePercent"]),
        "quote_volume_24h": float(ticker["quoteVolume"]),
        "funding_rate": funding_by.get(symbol),
    }


def last_closed_candle(klines: list[list], now: datetime | None = None) -> dict | None:
    now_ms = (now or datetime.now(timezone.utc)).timestamp() * 1000
    closed = [k for k in klines if k[6] < now_ms]
    if not closed:
        return None
    k = closed[-1]
    o, h, l, c = (float(x) for x in k[1:5])
    return {"open": o, "high": h, "low": l, "close": c, "change_pct": (c - o) / o * 100 if o else 0.0}


def funding_extremes(symbols: set[str], funding_by: dict[str, float], threshold: float = 0.0005) -> list[dict]:
    """Funding beyond ±0.05% per interval — crowded positioning worth flagging."""
    rows = [
        {"symbol": s, "funding_rate": funding_by[s]}
        for s in symbols
        if s in funding_by and abs(funding_by[s]) >= threshold
    ]
    return sorted(rows, key=lambda r: -abs(r["funding_rate"]))
