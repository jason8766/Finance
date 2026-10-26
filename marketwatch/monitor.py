"""24/7 monitor: move alerts, liquidation / OI spikes, memecoin rugs, macro event reminders.

    python -m marketwatch monitor

Runs forever (systemd keeps it alive). Every source is polled defensively: a
failure is logged and retried next tick, never fatal.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from . import alerts, telegram
from .brief import KST, _event_text, fmt_pct, fmt_price, fmt_usd
from .config import Settings
from .sources import binance, dex, econ_calendar, news
from .sources.liquidations import LiquidationStream

log = logging.getLogger(__name__)

TICK_S = 10                    # Binance price poll
SLOW_TICK_S = 60               # memecoins, open interest, reminders
UNIVERSE_REFRESH_S = 3600      # re-pick the top 20 by volume
MARKET_WIDE_MIN = 5            # this many simultaneous alerts become one message
LIQ_ALERT_USD = 50e6           # liquidations in 5 min
OI_ALERT_PCT = 3.0             # BTC open interest change in 15 min
OUTAGE_WARN_S = 120            # tell the user if Binance is unreachable this long


class Monitor:
    def __init__(self, settings: Settings, send=None, state_path: Path | None = None):
        self.settings = settings
        self.send = send or self._telegram_send
        self.perps = alerts.MoveDetector(alerts.perp_tier)
        self.memes = alerts.MoveDetector(lambda _: alerts.TIER_MEME)
        self.meme_liquidity = alerts.LiquidityWatch()
        self.liquidations = alerts.Liquidations()
        self.btc_oi = alerts.SeriesChange()
        self.liq_alarm = alerts.Threshold()
        self.oi_alarm = alerts.Threshold()
        self.top20: list[str] = []
        self.symbols: list[str] = []
        self.meme_info: dict[str, dict] = {}
        self.state_path = state_path or econ_calendar.default_cache_path().with_name("reminders.json")
        self.sent_reminders = self._load_sent()
        self._headlines: tuple[float, list[dict]] = (0.0, [])
        self.last_price_ok = time.time()
        self.outage_reported = False

    # ---------- main loop ----------

    def run(self) -> None:
        LiquidationStream(self.liquidations).start()
        self.refresh_universe()
        self.send(
            f"🟢 <b>Monitor started</b> — watching {len(self.symbols)} Binance perps and "
            f"{len(self.settings.memecoins)} Solana memecoins. Alerts begin after a 5-minute warm-up."
        )
        next_universe = time.time() + UNIVERSE_REFRESH_S
        next_slow = 0.0
        while True:
            started = time.time()
            if started >= next_universe:
                self._guard("universe", self.refresh_universe)
                next_universe = started + UNIVERSE_REFRESH_S
            self._guard("prices", lambda: self.tick_prices(started))
            self._guard("liquidations", lambda: self.tick_liquidations(started))
            if started >= next_slow:
                self._guard("open interest", lambda: self.tick_open_interest(started))
                self._guard("memecoins", lambda: self.tick_memes(started))
                self._guard("reminders", lambda: self.tick_reminders(datetime.now(KST)))
                next_slow = started + SLOW_TICK_S
            time.sleep(max(1.0, TICK_S - (time.time() - started)))

    def _guard(self, name: str, fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - keep monitoring whatever one source does
            log.exception("%s tick failed", name)

    def refresh_universe(self) -> None:
        top, watch, missing = binance.universe(self.settings.watch_perps)
        self.top20 = top
        self.symbols = list(dict.fromkeys(top + watch))
        if missing:
            log.warning("watchlist names not on Binance futures: %s", ", ".join(missing))
        log.info("monitoring %d perps", len(self.symbols))

    # ---------- ticks ----------

    def tick_prices(self, now: float) -> None:
        try:
            prices = binance.all_prices()
        except Exception:
            if now - self.last_price_ok > OUTAGE_WARN_S and not self.outage_reported:
                self.outage_reported = True
                self.send("⚠️ <b>Monitor can't reach Binance</b> — price alerts paused until it recovers.")
            raise
        self.last_price_ok = now
        if self.outage_reported:
            self.outage_reported = False
            self.send("✅ Binance reachable again — price alerts resumed.")
        moves = self.perps.update(now, {s: prices[s] for s in self.symbols if s in prices})
        if len(moves) >= MARKET_WIDE_MIN:
            self.send(self.format_market_wide(moves, now))
        else:
            for m in moves:
                self.send(self.format_move(m, now))

    def tick_liquidations(self, now: float) -> None:
        t = self.liquidations.totals(now, 300)
        if self.liq_alarm.fire(now, t["total"] >= LIQ_ALERT_USD):
            btc = self.perps.change_pct("BTCUSDT", now, 300)
            top = ", ".join(f"{_base_sym(s)} {fmt_usd(v)}" for s, v in t["top"])
            self.send(
                f"💥 <b>{fmt_usd(t['total'])} liquidated in 5 min</b>\n"
                f"Longs {fmt_usd(t['longs'])} / shorts {fmt_usd(t['shorts'])} · top: {escape(top)}"
                + (f"\nBTC 5m: {fmt_pct(btc)}" if btc is not None else "")
            )

    def tick_open_interest(self, now: float) -> None:
        self.btc_oi.add(now, binance.open_interest("BTCUSDT"))
        change = self.btc_oi.change_pct(now, 900)
        if change is not None and self.oi_alarm.fire(now, abs(change) >= OI_ALERT_PCT):
            price = self.perps.change_pct("BTCUSDT", now, 900)
            read = "leverage building — a squeeze in either direction gets likelier" if change > 0 \
                else "positions being closed / deleveraging"
            self.send(
                f"📈 <b>BTC open interest {fmt_pct(change)} in 15 min</b>"
                + (f" (price {fmt_pct(price)})" if price is not None else "")
                + f"\n<i>{read}</i>"
            )

    def tick_memes(self, now: float) -> None:
        rows = [r for r in dex.fetch_tokens(self.settings.memecoins) if r.get("found") and r.get("price_usd")]
        self.meme_info = {r["symbol"]: r for r in rows}
        for m in self.memes.update(now, {r["symbol"]: r["price_usd"] for r in rows}):
            self.send(self.format_meme_move(m))
        for r in rows:
            drop = self.meme_liquidity.update(now, r["symbol"], r.get("liquidity_usd"))
            if drop:
                peak, current = drop
                self.send(
                    f"🚨 <b>{escape(r['symbol'])} liquidity −{(1 - current / peak) * 100:.0f}% in under 1h</b>\n"
                    f"{fmt_usd(peak)} → {fmt_usd(current)} · price {fmt_price(r['price_usd'])}\n"
                    f"<i>Possible LP pull / rug — check before trading.</i>" + _link(r)
                )

    def tick_reminders(self, now: datetime) -> None:
        events = self.calendar_events()
        pre, post = alerts.due_reminders(events, now, self.sent_reminders)
        for group in pre:
            when = group[0]["time_kst"]
            mins = max(1, round((when - now).total_seconds() / 60))
            lines = [f"⏰ <b>In {mins} min ({when:%H:%M} KST)</b>"]
            lines += [f"🔴 {escape(_event_text(e, with_time=False))}" for e in group]
            lines.append("<i>Expect volatility around the release — check leverage and stops.</i>")
            self.send("\n".join(lines))
        for group in post:
            when = group[0]["time_kst"]
            since = (now - when).total_seconds()
            btc = self.perps.change_pct("BTCUSDT", time.time(), int(since))
            lines = [f"📊 <b>Released {when:%H:%M} KST</b>"]
            lines += [f"• {escape(_event_text(e, with_time=False))}" for e in group]
            if btc is not None:
                lines.append(f"BTC since release: {fmt_pct(btc)}")
            lines.append('<a href="https://www.forexfactory.com/calendar">Actual figures</a>')
            self.send("\n".join(lines))
        if pre or post:
            self._save_sent()

    # ---------- context for alerts ----------

    def calendar_events(self) -> list[dict]:
        s = self.settings
        return econ_calendar.select(econ_calendar.fetch_events(), s.calendar_all_impacts, s.calendar_high_only)

    def recent_events(self, now: datetime) -> list[dict]:
        """Macro releases from the last 30 min — the usual suspects for a sudden move."""
        try:
            return [e for e in self.calendar_events()
                    if timedelta(0) <= now - e["time_kst"] <= timedelta(minutes=30)]
        except Exception:  # noqa: BLE001 - context is best-effort
            log.exception("calendar context failed")
            return []

    def recent_headlines(self, now: float) -> list[dict]:
        fetched, items = self._headlines
        if now - fetched > 180:
            try:
                since = datetime.fromtimestamp(now, timezone.utc) - timedelta(minutes=60)
                items = news.fetch_headlines(self.settings.news_feeds, since, limit=20)
            except Exception:  # noqa: BLE001 - context is best-effort
                log.exception("headline context failed")
                items = []
            self._headlines = (now, items)
        return items

    def breadth(self, now: float, direction: int) -> tuple[int, int]:
        """How many top-20 coins moved >1% the same way in the last 5 min."""
        changes = [self.perps.change_pct(s, now, 300) for s in self.top20]
        changes = [c for c in changes if c is not None]
        same = sum(1 for c in changes if c * direction >= 1.0)
        return same, len(changes)

    # ---------- formatting ----------

    def format_move(self, m: alerts.Move, now: float) -> str:
        direction = 1 if m.pct > 0 else -1
        icon = "🟢" if direction > 0 else "🔴"
        lines = [f"{icon} <b>{_base_sym(m.symbol)} {fmt_pct(m.pct)} in {_took(m)}</b>",
                 f"{fmt_price(m.from_price)} → {fmt_price(m.to_price)}"]
        lines += self._context_lines(now, direction, m.symbol)
        return "\n".join(lines)

    def format_market_wide(self, moves: list[alerts.Move], now: float) -> str:
        down = sum(1 for m in moves if m.pct < 0)
        direction = -1 if down >= len(moves) - down else 1
        icon = "🔴" if direction < 0 else "🟢"
        moves = sorted(moves, key=lambda m: -abs(m.pct))
        lines = [f"{icon}🌊 <b>Market-wide {'drop' if direction < 0 else 'pump'}: {len(moves)} coins alerted</b>"]
        lines += [f"{_base_sym(m.symbol)} {fmt_pct(m.pct)} ({_took(m)})" for m in moves[:10]]
        if len(moves) > 10:
            lines.append(f"…and {len(moves) - 10} more")
        lines += self._context_lines(now, direction, None)
        return "\n".join(lines)

    def format_meme_move(self, m: alerts.Move) -> str:
        r = self.meme_info.get(m.symbol, {})
        icon = "🟢" if m.pct > 0 else "🔴"
        return (
            f"{icon}🐸 <b>{escape(m.symbol)} {fmt_pct(m.pct)} in {_took(m)}</b>\n"
            f"{fmt_price(m.from_price)} → {fmt_price(m.to_price)} · liq {fmt_usd(r.get('liquidity_usd'))} · "
            f"mcap {fmt_usd(r.get('market_cap_usd'))}" + _link(r)
        )

    def _context_lines(self, now: float, direction: int, symbol: str | None) -> list[str]:
        lines = []
        liq = self.liquidations.totals(now, 300)
        if liq["total"] > 0:
            lines.append(f"Liquidations 5m: longs {fmt_usd(liq['longs'])} / shorts {fmt_usd(liq['shorts'])}")
        oi = self.btc_oi.change_pct(now, 900)
        if oi is not None:
            lines.append(f"BTC OI 15m: {fmt_pct(oi)}")
        same, total = self.breadth(now, direction)
        if total:
            lines.append(f"Breadth: {same}/{total} top coins {'down' if direction < 0 else 'up'} >1% in 5m")

        events = self.recent_events(datetime.fromtimestamp(now, KST))
        for e in events:
            lines.append(f"📅 {e['time_kst']:%H:%M} KST {escape(_event_text(e, with_time=False))}")

        # A simple, honest read. Phase 4 replaces this with Claude's analysis.
        same_side_liq = liq["longs"] if direction < 0 else liq["shorts"]
        if events:
            read = f"likely reaction to {events[0]['country']} {events[0]['title']}"
        elif same_side_liq >= 20e6:
            read = "liquidation-driven — forced " + ("long" if direction < 0 else "short") + " closures"
        elif same >= max(3, total / 2):
            read = "market-wide move, no scheduled catalyst found"
        elif symbol:
            read = "coin-specific move, no scheduled catalyst found"
        else:
            read = "no scheduled catalyst found"
        lines.append(f"<i>Read: {escape(read)}</i>")

        headlines = self.recent_headlines(now)
        if symbol:
            base = _base_sym(symbol)
            pattern = re.compile(rf"\b{re.escape(base)}\b", re.IGNORECASE)
            headlines = sorted(headlines, key=lambda h: not pattern.search(h["title"]))
        if headlines:
            lines.append("📰 Last hour:")
            lines += [f"• {escape(h['title'])} <i>({escape(h['source'])})</i>" for h in headlines[:3]]
        return lines

    # ---------- plumbing ----------

    def _telegram_send(self, html: str) -> None:
        try:
            telegram.send(self.settings.telegram_token, self.settings.telegram_chat_id, html)
        except Exception:  # noqa: BLE001 - a failed send must not stop monitoring
            log.exception("telegram send failed")

    def _load_sent(self) -> set[str]:
        try:
            return set(json.loads(self.state_path.read_text()))
        except (OSError, ValueError):
            return set()

    def _save_sent(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=2)
        keep = {k for k in self.sent_reminders if datetime.fromisoformat(k.split(":", 1)[1]) >= cutoff}
        self.sent_reminders = keep
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(sorted(keep)))
        except OSError:
            log.exception("could not save reminder state")


def _base_sym(symbol: str) -> str:
    return symbol.removesuffix("USDT").removesuffix("USDC")


def _took(m: alerts.Move) -> str:
    return "under 1 min" if m.took_min < 1 else f"{round(m.took_min)} min"


def _link(r: dict) -> str:
    return f'\n<a href="{escape(r["url"])}">Chart</a>' if r.get("url") else ""


def run(settings: Settings) -> None:
    Monitor(settings).run()
