from datetime import datetime, timedelta, timezone

from marketwatch import alerts

KST = timezone(timedelta(hours=9))


def feed(detector, symbol, prices, start=0.0, step=10.0):
    """Feed a price series at `step`-second intervals; return all moves raised."""
    moves = []
    for i, p in enumerate(prices):
        moves += detector.update(start + i * step, {symbol: p})
    return moves


def test_no_alerts_during_warmup():
    d = alerts.MoveDetector(alerts.perp_tier)
    # A 5% crash in the first minute: not enough history to judge the 5-min window yet.
    assert feed(d, "BTCUSDT", [100, 100, 95, 95, 95]) == []


def test_btc_drop_in_5m_triggers_once_with_cooldown():
    d = alerts.MoveDetector(alerts.perp_tier)
    prices = [100.0] * 30 + [99.0, 98.4, 98.3] + [98.3] * 20
    moves = feed(d, "BTCUSDT", prices)
    assert len(moves) == 1
    m = moves[0]
    assert m.window_min == 5 and round(m.pct, 1) == -1.6 and m.from_price == 100.0


def test_cooldown_lifts_when_move_extends_another_step():
    d = alerts.MoveDetector(alerts.perp_tier)
    prices = [100.0] * 30 + [98.4] * 3 + [96.8] * 3   # -1.6%, then another -1.6%
    moves = feed(d, "BTCUSDT", prices)
    assert [round(m.to_price, 1) for m in moves] == [98.4, 96.8]


def test_alt_needs_bigger_move_and_pump_detected():
    d = alerts.MoveDetector(alerts.perp_tier)
    assert feed(d, "ZECUSDT", [100.0] * 30 + [102.5] * 3) == []          # 2.5% < 3% alt threshold
    d = alerts.MoveDetector(alerts.perp_tier)
    moves = feed(d, "ZECUSDT", [100.0] * 30 + [103.2] * 3)
    assert len(moves) == 1 and moves[0].pct > 3


def test_slow_1h_grind_triggers_1h_window():
    d = alerts.MoveDetector(alerts.perp_tier)
    # BTC drifts down 3.3% over an hour in small steps: never 1.5% within 5 min.
    prices = [100 - 0.01 * i for i in range(361)]
    moves = feed(d, "BTCUSDT", prices)
    assert moves and moves[0].window_min == 60 and moves[0].pct <= -3


def test_change_pct_point_to_point():
    d = alerts.MoveDetector(alerts.perp_tier)
    feed(d, "ETHUSDT", [100.0] * 30 + [101.0])
    assert round(d.change_pct("ETHUSDT", 300.0, 300), 2) == 1.0
    assert d.change_pct("NOPE", 300.0, 300) is None


def test_liquidity_watch_flags_lp_pull_once():
    w = alerts.LiquidityWatch()
    assert w.update(0, "KIMCHI", 100_000) is None
    assert w.update(60, "KIMCHI", 90_000) is None
    assert w.update(120, "KIMCHI", 60_000) == (100_000, 60_000)
    assert w.update(180, "KIMCHI", 50_000) is None  # cooldown


def test_liquidation_totals_window_and_sides():
    liq = alerts.Liquidations()
    liq.add(0, 10e6, "SELL", "BTCUSDT")       # too old for a 5-min window at t=400
    liq.add(200, 30e6, "SELL", "BTCUSDT")
    liq.add(300, 25e6, "SELL", "ETHUSDT")
    liq.add(350, 5e6, "BUY", "BTCUSDT")
    t = liq.totals(400, 300)
    assert t["longs"] == 55e6 and t["shorts"] == 5e6 and t["total"] == 60e6
    assert t["top"][0] == ("BTCUSDT", 35e6)


def test_threshold_cooldown():
    th = alerts.Threshold(cooldown_s=100)
    assert th.fire(0, True) and not th.fire(50, True) and not th.fire(150, False) and th.fire(150, True)


def test_series_change():
    s = alerts.SeriesChange()
    for i in range(16):
        s.add(i * 60, 100 + i * 0.25)  # +3.75 over 15 min
    assert round(s.change_pct(900, 900), 2) == 3.75


def test_due_reminders_groups_and_fires_once():
    release = datetime(2026, 10, 27, 21, 30, tzinfo=KST)
    events = [
        {"time_kst": release, "impact": "High", "country": "USD", "title": "CPI m/m"},
        {"time_kst": release, "impact": "High", "country": "USD", "title": "Core CPI m/m"},
        {"time_kst": release, "impact": "Medium", "country": "USD", "title": "Real Earnings"},
    ]
    sent: set[str] = set()
    pre, post = alerts.due_reminders(events, release - timedelta(minutes=45), sent)
    assert pre == [] and post == []
    pre, post = alerts.due_reminders(events, release - timedelta(minutes=30), sent)
    assert [len(g) for g in pre] == [2] and post == []
    assert alerts.due_reminders(events, release - timedelta(minutes=20), sent) == ([], [])
    pre, post = alerts.due_reminders(events, release + timedelta(minutes=3), sent)
    assert pre == [] and [len(g) for g in post] == [2]
    assert alerts.due_reminders(events, release + timedelta(minutes=5), sent) == ([], [])


def test_took_measures_time_since_last_high_not_window_start():
    d = alerts.MoveDetector(alerts.perp_tier)
    moves = feed(d, "BTCUSDT", [100.0] * 30 + [97.0])
    assert moves and moves[0].took_min < 0.5
