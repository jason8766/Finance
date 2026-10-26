"""Daily 09:00 KST brief: collect data, ask Claude for commentary, render Telegram HTML."""

import logging
import math
from datetime import datetime, timedelta, timezone
from html import escape

from . import llm
from .config import Settings
from .sources import binance, dex, econ_calendar, news, sentiment

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
IMPACT_ICON = {"High": "🔴", "Medium": "🟠"}


def collect(settings: Settings, now: datetime | None = None) -> dict:
    """Gather every source; a failing source is recorded, never fatal."""
    now = now or datetime.now(KST)
    data: dict = {"now_kst": now, "errors": []}

    def attempt(name, fn):
        try:
            data[name] = fn()
        except Exception as e:  # noqa: BLE001 - one bad source must not kill the brief
            log.exception("source %s failed", name)
            data["errors"].append(f"{name}: {type(e).__name__}")
            data[name] = None

    attempt("market", lambda: binance.fetch_market(settings.watch_perps))
    attempt("fear_greed", sentiment.fear_greed)
    attempt("global", sentiment.global_market)
    attempt("memecoins", lambda: dex.fetch_tokens(settings.memecoins))
    attempt("headlines", lambda: news.fetch_headlines(settings.news_feeds, news.last_24h()))
    attempt("calendar", lambda: econ_calendar.select(
        econ_calendar.fetch_events(), settings.calendar_all_impacts, settings.calendar_high_only))

    today, week = econ_calendar.split_today_week(data["calendar"] or [], now)
    data["today_events"], data["week_events"] = today, week
    return data


def llm_input(data: dict) -> dict:
    """The subset of collected data the model needs (keeps tokens and cost down)."""
    market = data.get("market") or {}
    return {
        "now_kst": data["now_kst"].strftime("%a %d %b %Y %H:%M KST"),
        "headlines": data.get("headlines") or [],
        "calendar_next_24h": [_event_text(e) for e in data["today_events"]],
        "calendar_week_ahead_high_impact": [_event_text(e) for e in data["week_events"]],
        "majors": market.get("majors"),
        "funding_extremes": market.get("funding_extremes"),
        "fear_greed": data.get("fear_greed"),
        "global": data.get("global"),
    }


def build(settings: Settings, use_llm: bool = True, now: datetime | None = None) -> str:
    data = collect(settings, now)
    sections = llm.write_sections(llm_input(data), settings.anthropic_api_key, settings.llm_model) if use_llm else None
    return render(data, sections)


# ---------- rendering ----------

def render(data: dict, ai: dict | None) -> str:
    now = data["now_kst"]
    parts = [f"☀️ <b>Daily Brief</b> — {now:%a %d %b %Y}"]
    parts.append(_market_section(data))
    parts.append(_news_section(data, ai))
    parts.append(_today_section(data, ai))
    if data["week_events"]:
        parts.append(_week_section(data))
    risks = _risk_section(data, ai)
    if risks:
        parts.append(risks)
    watch = _watchlist_section(data)
    if watch:
        parts.append(watch)
    memes = _meme_section(data)
    if memes:
        parts.append(memes)
    if data["errors"]:
        parts.append("<i>⚠️ Unavailable this run: " + escape(", ".join(data["errors"])) + "</i>")
    return "\n\n".join(p for p in parts if p)


def _market_section(data: dict) -> str:
    market = data.get("market")
    lines = ["<b>📊 Market</b>"]
    if market:
        for sym, m in market["majors"].items():
            lines.append(
                f"<b>{sym.removesuffix('USDT')}</b> {fmt_price(m['price'])} ({fmt_pct(m['change_24h_pct'])}) · "
                f"funding {fmt_funding(m['funding_rate'])} · OI {fmt_usd(m['open_interest_usd'])}"
            )
        candles = [
            f"{sym.removesuffix('USDT')} {fmt_pct(m['yesterday_candle']['change_pct'])}"
            for sym, m in market["majors"].items() if m.get("yesterday_candle")
        ]
        if candles:
            lines.append("Yesterday's daily candle: " + " · ".join(candles))
        movers = sorted(market["top20"], key=lambda r: r["change_24h_pct"])
        if movers:
            up = [r for r in reversed(movers[-3:]) if r["change_24h_pct"] > 0]
            down = [r for r in movers[:3] if r["change_24h_pct"] < 0]
            mv = []
            if up:
                mv.append("▲ " + ", ".join(f"{_base(r)} {fmt_pct(r['change_24h_pct'])}" for r in up))
            if down:
                mv.append("▼ " + ", ".join(f"{_base(r)} {fmt_pct(r['change_24h_pct'])}" for r in down))
            if mv:
                lines.append("Top-20 movers: " + " | ".join(mv))
    ctx = []
    if g := data.get("global"):
        ctx.append(f"BTC dom {g['btc_dominance_pct']:.1f}%")
        ctx.append(f"Total cap {fmt_usd(g['total_market_cap_usd'])} ({fmt_pct(g['market_cap_change_24h_pct'])})")
    if fg := data.get("fear_greed"):
        prev = f", was {fg['previous']}" if fg.get("previous") is not None else ""
        ctx.append(f"Fear &amp; Greed {fg['value']} {escape(fg['label'])}{prev}")
    if ctx:
        lines.append(" · ".join(ctx))
    if len(lines) == 1:
        lines.append("<i>Market data unavailable.</i>")
    return "\n".join(lines)


def _news_section(data: dict, ai: dict | None) -> str:
    lines = ["<b>📰 Last 24h</b>"]
    if ai and ai.get("yesterday"):
        for item in ai["yesterday"]:
            lines.append(f"• {escape(item['headline'])} — <i>{escape(item['why_it_matters'])}</i>")
    elif data.get("headlines"):
        # No AI commentary available: show the latest raw headlines instead.
        for h in data["headlines"][:8]:
            lines.append(f"• {escape(h['title'])} <i>({escape(h['source'])})</i>")
    else:
        lines.append("<i>No headlines available.</i>")
    return "\n".join(lines)


def _today_section(data: dict, ai: dict | None) -> str:
    lines = ["<b>📅 Next 24h (KST)</b>"]
    if data.get("calendar") is None:
        lines.append("<i>Calendar unavailable.</i>")
    elif not data["today_events"]:
        lines.append("No high/medium-impact macro events scheduled.")
    for e in data["today_events"]:
        lines.append(f"{IMPACT_ICON.get(e['impact'], '⚪')} {e['time_kst']:%H:%M} {escape(_event_text(e, with_time=False))}")
    if ai and ai.get("today_focus"):
        lines.append(f"<i>{escape(ai['today_focus'])}</i>")
    return "\n".join(lines)


def _week_section(data: dict) -> str:
    lines = ["<b>🗓 Week ahead (high impact)</b>"]
    for e in data["week_events"][:12]:
        lines.append(f"{e['time_kst']:%a %H:%M} {escape(_event_text(e, with_time=False))}")
    return "\n".join(lines)


def _risk_section(data: dict, ai: dict | None) -> str:
    lines = []
    if ai:
        lines += [f"• {escape(r)}" for r in ai.get("risk_flags") or []]
    extremes = ((data.get("market") or {}).get("funding_extremes")) or []
    if extremes:
        lines.append("• Funding extremes: " + ", ".join(
            f"{_base(r)} {fmt_funding(r['funding_rate'])}" for r in extremes[:6]))
    return "\n".join(["<b>⚠️ Risk flags</b>"] + lines) if lines else ""


def _watchlist_section(data: dict) -> str:
    market = data.get("market") or {}
    rows = sorted(market.get("watchlist") or [], key=lambda r: -abs(r["change_24h_pct"]))
    if not rows and not market.get("watchlist_missing"):
        return ""
    lines = ["<b>👀 Watchlist (24h)</b>"]
    lines += [f"{_base(r)} {fmt_price(r['price'])} {fmt_pct(r['change_24h_pct'])}" for r in rows]
    if market.get("watchlist_missing"):
        lines.append("<i>Not found on Binance futures: " + escape(", ".join(market["watchlist_missing"])) + "</i>")
    return "\n".join(lines)


def _meme_section(data: dict) -> str:
    coins = data.get("memecoins") or []
    if not coins:
        return ""
    lines = ["<b>🐸 Solana memes</b>"]
    for c in coins:
        name = escape(c["symbol"])
        if not c.get("found"):
            lines.append(f"{name}: no DEX pair found")
            continue
        label = f'<a href="{escape(c["url"])}">{name}</a>' if c.get("url") else name
        lines.append(
            f"{label} {fmt_price(c['price_usd'])} {fmt_pct(c['change_24h_pct'])} · "
            f"liq {fmt_usd(c['liquidity_usd'])} · mcap {fmt_usd(c['market_cap_usd'])}"
        )
    return "\n".join(lines)


# ---------- formatting helpers ----------

def _base(row: dict) -> str:
    return row["symbol"].removesuffix("USDT").removesuffix("USDC")


def _event_text(e: dict, with_time: bool = True) -> str:
    text = f"{e['country']} {e['title']}"
    extra = [f"f {e['forecast']}" if e.get("forecast") else "", f"p {e['previous']}" if e.get("previous") else ""]
    extra = [x for x in extra if x]
    if extra:
        text += " (" + " / ".join(extra) + ")"
    if with_time:
        text = f"{e['time_kst']:%a %H:%M} KST {text} [{e['impact']}]"
    return text


def fmt_price(p: float | None) -> str:
    if p is None:
        return "n/a"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:,.2f}"
    if p <= 0:
        return "$0"
    decimals = -math.floor(math.log10(p)) + 3  # 4 significant digits, no scientific notation
    return f"${p:.{decimals}f}"


def fmt_pct(p: float | None) -> str:
    if p is None:
        return "n/a"
    return f"{'+' if p >= 0 else '−'}{abs(p):.1f}%"


def fmt_funding(rate: float | None) -> str:
    if rate is None:
        return "n/a"
    return f"{'+' if rate >= 0 else '−'}{abs(rate) * 100:.3f}%"


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:.2f}{suffix}" if div >= 1e9 else f"${v / div:.1f}{suffix}"
    return f"${v:,.0f}"
