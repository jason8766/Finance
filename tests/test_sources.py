from datetime import datetime, timedelta, timezone

from marketwatch.sources import binance, dex, econ_calendar

KST = timezone(timedelta(hours=9))


def perp(symbol, base, quote="USDT", contract="PERPETUAL", status="TRADING"):
    return {"symbol": symbol, "baseAsset": base, "quoteAsset": quote, "contractType": contract, "status": status}


INFO = {"symbols": [
    perp("BTCUSDT", "BTC"),
    perp("ETHUSDT", "ETH"),
    perp("USDCUSDT", "USDC"),
    perp("1000PEPEUSDT", "1000PEPE"),
    perp("XAUUSDT", "XAU", contract="TRADIFI_PERPETUAL"),
    perp("SNDKUSDC", "SNDK", quote="USDC"),
    perp("BTCUSDT_261225", "BTC", contract="CURRENT_QUARTER"),
    perp("OLDUSDT", "OLD", status="SETTLING"),
]}


def test_tradable_perps_keeps_only_trading_perpetuals():
    perps = binance.tradable_perps(INFO)
    assert set(perps) == {"BTCUSDT", "ETHUSDT", "USDCUSDT", "1000PEPEUSDT", "XAUUSDT", "SNDKUSDC"}


def test_resolve_bases_handles_multiplier_prefix_and_quote_fallback():
    resolved, missing = binance.resolve_bases(["BTC", "PEPE", "XAU", "SNDK", "NOPE"], binance.tradable_perps(INFO))
    assert resolved == {"BTC": "BTCUSDT", "PEPE": "1000PEPEUSDT", "XAU": "XAUUSDT", "SNDK": "SNDKUSDC"}
    assert missing == ["NOPE"]


def test_top_by_volume_excludes_stablecoins_and_non_usdt():
    perps = binance.tradable_perps(INFO)
    tickers = {s: {"quoteVolume": str(v)} for s, v in
               [("BTCUSDT", 900), ("USDCUSDT", 5000), ("ETHUSDT", 500), ("SNDKUSDC", 9999), ("1000PEPEUSDT", 100)]}
    assert binance.top_by_volume(perps, tickers, n=2) == ["BTCUSDT", "ETHUSDT"]


def test_last_closed_candle_ignores_open_candle():
    now = datetime(2026, 10, 26, 0, 0, 30, tzinfo=timezone.utc)
    day = 86_400_000
    start = int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp() * 1000)
    klines = [
        [start, "100", "110", "90", "105", "0", start + day - 1],
        [start + day, "105", "106", "104", "105.5", "0", start + 2 * day - 1],
    ]
    c = binance.last_closed_candle(klines, now)
    assert c["close"] == 105 and round(c["change_pct"], 2) == 5.0


def test_funding_extremes_threshold_and_sort():
    rows = binance.funding_extremes({"A", "B", "C"}, {"A": 0.0001, "B": -0.002, "C": 0.0009})
    assert [r["symbol"] for r in rows] == ["B", "C"]


def test_calendar_select_and_split():
    raw = [
        {"title": "CPI m/m", "country": "USD", "date": "2026-10-27T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.4%"},
        {"title": "Retail Sales", "country": "USD", "date": "2026-10-27T10:00:00-04:00", "impact": "Medium", "forecast": "", "previous": ""},
        {"title": "German Ifo", "country": "EUR", "date": "2026-10-27T05:00:00-04:00", "impact": "Medium"},
        {"title": "BOJ Rate", "country": "JPY", "date": "2026-10-30T00:00:00-04:00", "impact": "High"},
        {"title": "Bank Holiday", "country": "USD", "date": "2026-10-28T00:00:00-04:00", "impact": "Holiday"},
    ]
    events = econ_calendar.select(raw, ["USD"], ["JPY", "EUR"])
    assert [e["title"] for e in events] == ["CPI m/m", "Retail Sales", "BOJ Rate"]
    assert events[0]["time_kst"].strftime("%H:%M") == "21:30"
    assert events[1]["forecast"] is None

    now = datetime(2026, 10, 27, 9, 0, tzinfo=KST)
    today, week = econ_calendar.split_today_week(events, now)
    assert [e["title"] for e in today] == ["CPI m/m", "Retail Sales"]  # 21:30 and 23:00 KST
    assert [e["title"] for e in week] == ["BOJ Rate"]


def test_dex_best_pair_picks_deepest_liquidity_for_mint():
    mint = "MINT111pump"
    pairs = [
        {"chainId": "solana", "baseToken": {"address": mint}, "priceUsd": "0.01", "liquidity": {"usd": 1000}, "priceChange": {"h24": 5}},
        {"chainId": "solana", "baseToken": {"address": mint}, "priceUsd": "0.02", "liquidity": {"usd": 50000}, "priceChange": {"h24": -3}, "url": "u"},
        {"chainId": "solana", "baseToken": {"address": "OTHER"}, "priceUsd": "9", "liquidity": {"usd": 10**9}},
    ]
    row = dex.best_pair({"symbol": "X", "mint": mint}, pairs)
    assert row["found"] and row["price_usd"] == 0.02 and row["change_24h_pct"] == -3
    assert dex.best_pair({"symbol": "Y", "mint": "NONE"}, pairs)["found"] is False


def test_calendar_cache_ttl_fallback_and_merge(tmp_path, monkeypatch):
    path = tmp_path / "calendar.json"
    t0 = datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)
    week1 = [{"title": "CPI", "country": "USD", "date": "2026-10-27T08:30:00-04:00", "impact": "High"}]
    old = [{"title": "Old", "country": "USD", "date": "2026-10-01T08:30:00-04:00", "impact": "High"}]
    calls = []

    def download(result):
        def fn():
            calls.append(1)
            return None if result is None else [dict(r) for r in result]
        return fn

    # No cache and feed down: a clear error, not a silent empty calendar.
    monkeypatch.setattr(econ_calendar, "_download", download(None))
    try:
        econ_calendar.fetch_events(path, t0)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass

    monkeypatch.setattr(econ_calendar, "_download", download(week1 + old))
    assert [e["title"] for e in econ_calendar.fetch_events(path, t0)] == ["CPI"]  # stale row pruned

    # Within the TTL the feed is not hit again.
    calls.clear()
    econ_calendar.fetch_events(path, t0 + timedelta(minutes=30))
    assert calls == []

    # After the TTL a refused download (429) falls back to the cached copy.
    monkeypatch.setattr(econ_calendar, "_download", download(None))
    assert [e["title"] for e in econ_calendar.fetch_events(path, t0 + timedelta(hours=2))] == ["CPI"]
