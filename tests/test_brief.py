import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from marketwatch import brief, llm, telegram
from marketwatch.config import Settings

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 27, 9, 0, 30, tzinfo=KST)


def row(symbol, price, chg, funding=0.0001):
    return {"symbol": symbol, "price": price, "change_24h_pct": chg, "quote_volume_24h": 1e9, "funding_rate": funding}


def fake_sources(monkeypatch, fail=()):
    def maybe(name, value):
        def fn(*a, **k):
            if name in fail:
                raise RuntimeError("boom")
            return value
        return fn

    btc = row("BTCUSDT", 67210.5, -1.23) | {"open_interest_usd": 9.8e9, "yesterday_candle": {"change_pct": -0.8}}
    market = {
        "majors": {"BTCUSDT": btc},
        "top20": [row("BTCUSDT", 67210.5, -1.2), row("DOGEUSDT", 0.21, 8.4), row("SUIUSDT", 3.1, -6.0)],
        "watchlist": [row("ZECUSDT", 412.3, 5.2), row("XAUUSDT", 2650, -0.4)],
        "watchlist_missing": ["SKR"],
        "funding_extremes": [{"symbol": "ZECUSDT", "funding_rate": 0.0012}],
    }
    events = [{"time_kst": NOW + timedelta(hours=12, minutes=30), "country": "USD", "title": "CPI m/m",
               "impact": "High", "forecast": "0.3%", "previous": "0.4%"}]
    monkeypatch.setattr(brief.binance, "fetch_market", maybe("market", market))
    monkeypatch.setattr(brief.sentiment, "fear_greed", maybe("fear_greed", {"value": 42, "label": "Fear", "previous": 48}))
    monkeypatch.setattr(brief.sentiment, "global_market", maybe("global", {
        "btc_dominance_pct": 57.12, "total_market_cap_usd": 2.41e12, "market_cap_change_24h_pct": -0.9}))
    monkeypatch.setattr(brief.dex, "fetch_tokens", maybe("memecoins", [
        {"symbol": "KIMCHI", "found": True, "price_usd": 0.00123, "change_24h_pct": -12.4,
         "liquidity_usd": 210_000, "market_cap_usd": 1.2e6, "url": "https://dexscreener.com/solana/x"},
        {"symbol": "NEET", "found": False}]))
    monkeypatch.setattr(brief.news, "fetch_headlines", maybe("headlines", [
        {"title": "SEC & CFTC <joint> statement", "source": "coindesk.com", "published_utc": "x", "summary": ""}]))
    monkeypatch.setattr(brief.econ_calendar, "fetch_events", maybe("calendar", []))
    monkeypatch.setattr(brief.econ_calendar, "select", lambda *a: events)


def test_render_without_llm_is_complete_and_escaped(monkeypatch):
    fake_sources(monkeypatch)
    text = brief.build(Settings(), use_llm=False, now=NOW)
    assert "Daily Brief</b> — Tue 27 Oct 2026" in text
    assert "<b>BTC</b> $67,210 (−1.2%) · funding +0.010% · OI $9.80B" in text
    assert "Top-20 movers: ▲ DOGE +8.4% | ▼ SUI −6.0%, BTC −1.2%" in text
    assert "SEC &amp; CFTC &lt;joint&gt; statement" in text  # headline fallback, escaped
    assert "🔴 21:30 USD CPI m/m (f 0.3% / p 0.4%)" in text
    assert "Funding extremes: ZEC +0.120%" in text
    assert "Not found on Binance futures: SKR" in text
    assert "KIMCHI</a> $0.001230 −12.4%" in text and "NEET: no DEX pair found" in text
    assert "Unavailable" not in text


def test_failing_source_is_reported_not_fatal(monkeypatch):
    fake_sources(monkeypatch, fail={"market", "headlines"})
    text = brief.build(Settings(), use_llm=False, now=NOW)
    assert "Market data unavailable" not in text  # fear/greed + global still render
    assert "Unavailable this run: market: RuntimeError, headlines: RuntimeError" in text


def test_llm_sections_used_and_escaped(monkeypatch):
    fake_sources(monkeypatch)
    ai = {"yesterday": [{"headline": "Fed <hawkish>", "why_it_matters": "Rates up & risk down"}],
          "today_focus": "CPI at 21:30 is the key event.", "risk_flags": ["Crowded ZEC longs"]}
    monkeypatch.setattr(brief.llm, "write_sections", lambda *a: ai)
    text = brief.build(Settings(anthropic_api_key="k"), now=NOW)
    assert "• Fed &lt;hawkish&gt; — <i>Rates up &amp; risk down</i>" in text
    assert "<i>CPI at 21:30 is the key event.</i>" in text
    assert "• Crowded ZEC longs" in text


def test_write_sections_request_and_parse(monkeypatch):
    captured = {}
    payload = {"yesterday": [], "today_focus": "Quiet day.", "risk_flags": []}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(stop_reason="end_turn", stop_details=None, content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=json.dumps(payload))])

    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **k: fake_client)

    assert llm.write_sections({"x": 1}, "key", "claude-opus-5-5") == payload
    assert captured["model"] == "claude-opus-5-5"
    assert captured["fallbacks"] == "default"
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["output_config"]["format"]["type"] == "json_schema"

    captured.clear()
    llm.write_sections({"x": 1}, "key", "claude-haiku-5-5")
    assert "fallbacks" not in captured


def test_write_sections_refusal_returns_none(monkeypatch):
    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(stop_reason="refusal", stop_details={"category": None}, content=[])

    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **k: SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
    assert llm.write_sections({}, "key", "claude-opus-5-5") is None
    assert llm.write_sections({}, "", "claude-opus-5-5") is None  # no key: skip the call


def test_telegram_split_respects_limit():
    sections = ["<b>S%d</b>\n" % i + "\n".join("line %d %s" % (j, "x" * 50) for j in range(30)) for i in range(5)]
    text = "\n\n".join(sections)
    chunks = telegram.split(text, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_strip_tags():
    assert telegram.strip_tags("<b>A &amp; B</b> &lt;x&gt;") == "A & B <x>"


def test_formatters():
    assert brief.fmt_price(0.000012345) == "$0.00001234"
    assert brief.fmt_price(0.5) == "$0.5000"
    assert brief.fmt_price(1.5) == "$1.50"
    assert brief.fmt_usd(2.41e12) == "$2.41T"
    assert brief.fmt_usd(210_000) == "$210.0K"
    assert brief.fmt_funding(-0.00025) == "−0.025%"
