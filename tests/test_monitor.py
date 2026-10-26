from datetime import datetime, timedelta, timezone

import pytest

from marketwatch import monitor
from marketwatch.config import Settings

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc).timestamp()   # 21:00 KST


@pytest.fixture
def mon(monkeypatch, tmp_path):
    sent = []
    settings = Settings(memecoins=[{"symbol": "KIMCHI", "mint": "M"}], news_feeds=["x"])
    m = monitor.Monitor(settings, send=sent.append, state_path=tmp_path / "reminders.json")
    m.top20 = [f"C{i}USDT" for i in range(10)] + ["BTCUSDT"]
    m.symbols = m.top20 + ["ZECUSDT"]
    m.sent = sent
    monkeypatch.setattr(monitor.news, "fetch_headlines", lambda *a, **k: [
        {"title": "Fed holds rates", "source": "cnbc.com"},
        {"title": "ZEC rallies on privacy demand", "source": "theblock.co"}])
    monkeypatch.setattr(monitor.econ_calendar, "fetch_events", lambda: [])
    monkeypatch.setattr(monitor.econ_calendar, "select", lambda *a: [])
    return m


def run_prices(m, monkeypatch, series: dict[str, list[float]], start=T0):
    n = len(next(iter(series.values())))
    for i in range(n):
        monkeypatch.setattr(monitor.binance, "all_prices", lambda i=i: {s: p[i] for s, p in series.items()})
        m.tick_prices(start + i * 10)


def test_single_coin_move_alert_with_context(mon, monkeypatch):
    flat = [100.0] * 33
    series = {s: flat for s in mon.top20}
    series["ZECUSDT"] = [100.0] * 30 + [104.0] * 3
    run_prices(mon, monkeypatch, series)
    assert len(mon.sent) == 1
    msg = mon.sent[0]
    assert "🟢 <b>ZEC +4.0% in" in msg
    assert "$100.00 → $104.00" in msg
    assert "coin-specific move, no scheduled catalyst found" in msg
    assert msg.index("ZEC rallies") < msg.index("Fed holds")  # coin headline first


def test_many_simultaneous_moves_become_one_market_wide_alert(mon, monkeypatch):
    drop = [100.0] * 30 + [95.0] * 3
    series = {s: drop for s in mon.symbols}
    run_prices(mon, monkeypatch, series)
    assert len(mon.sent) == 1
    msg = mon.sent[0]
    assert "Market-wide drop" in msg and "Breadth: 11/11 top coins down" in msg
    assert "market-wide move, no scheduled catalyst found" in msg


def test_macro_release_named_as_likely_catalyst(mon, monkeypatch):
    release = datetime.fromtimestamp(T0 + 290, KST)
    event = {"time_kst": release, "country": "USD", "title": "CPI m/m", "impact": "High",
             "forecast": "0.3%", "previous": "0.4%"}
    monkeypatch.setattr(monitor.econ_calendar, "select", lambda *a: [event])
    series = {s: [100.0] * 33 for s in mon.top20}
    series["BTCUSDT"] = [100.0] * 30 + [98.0] * 3
    run_prices(mon, monkeypatch, series)
    assert len(mon.sent) == 1
    assert "likely reaction to USD CPI m/m" in mon.sent[0]
    assert "USD CPI m/m (f 0.3% / p 0.4%)" in mon.sent[0]


def test_outage_warning_then_recovery(mon, monkeypatch):
    def down():
        raise ConnectionError("unreachable")

    monkeypatch.setattr(monitor.binance, "all_prices", down)
    mon.last_price_ok = T0
    for t in (T0 + 60, T0 + 130, T0 + 200):
        with pytest.raises(ConnectionError):
            mon.tick_prices(t)
    assert len(mon.sent) == 1 and "can't reach Binance" in mon.sent[0]
    monkeypatch.setattr(monitor.binance, "all_prices", lambda: {"BTCUSDT": 100.0})
    mon.tick_prices(T0 + 210)
    assert "reachable again" in mon.sent[-1]


def test_reminders_sent_once_even_across_restart(mon, monkeypatch, tmp_path):
    release = datetime(2026, 10, 27, 21, 30, tzinfo=KST)
    event = {"time_kst": release, "country": "USD", "title": "CPI m/m", "impact": "High",
             "forecast": "0.3%", "previous": "0.4%"}
    monkeypatch.setattr(monitor.econ_calendar, "select", lambda *a: [event])
    mon.tick_reminders(release - timedelta(minutes=30))
    assert len(mon.sent) == 1 and "In 30 min (21:30 KST)" in mon.sent[0]

    restarted = monitor.Monitor(mon.settings, send=mon.sent.append, state_path=tmp_path / "reminders.json")
    restarted.tick_reminders(release - timedelta(minutes=29))
    assert len(mon.sent) == 1
    restarted.tick_reminders(release + timedelta(minutes=4))
    assert "Released 21:30 KST" in mon.sent[-1]


def test_memecoin_move_and_rug_warning(mon, monkeypatch):
    def tokens(price, liq):
        return lambda coins: [{"symbol": "KIMCHI", "found": True, "price_usd": price,
                               "liquidity_usd": liq, "market_cap_usd": 1e6, "url": "https://dex/x"}]

    for i in range(6):
        monkeypatch.setattr(monitor.dex, "fetch_tokens", tokens(0.001, 200_000))
        mon.tick_memes(T0 + i * 60)
    monkeypatch.setattr(monitor.dex, "fetch_tokens", tokens(0.00085, 120_000))
    mon.tick_memes(T0 + 360)
    assert any("🔴🐸 <b>KIMCHI −15.0%" in s for s in mon.sent)
    assert any("KIMCHI liquidity −40%" in s for s in mon.sent)
