"""Pure alert logic: move detection, cooldowns, liquidation / OI trackers, event reminders.

No network here, so everything is unit-testable with synthetic prices and clocks.
"""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

COOLDOWN_S = 30 * 60


@dataclass(frozen=True)
class Tier:
    pct_5m: float
    pct_1h: float


# Medium sensitivity (docs/SPEC.md).
TIER_BTC = Tier(1.5, 3.0)
TIER_MAJOR = Tier(2.0, 4.0)
TIER_ALT = Tier(3.0, 6.0)
TIER_MEME = Tier(10.0, 20.0)


def perp_tier(symbol: str) -> Tier:
    if symbol == "BTCUSDT":
        return TIER_BTC
    if symbol in ("ETHUSDT", "SOLUSDT"):
        return TIER_MAJOR
    return TIER_ALT


@dataclass
class Move:
    symbol: str
    window_min: int     # 5 or 60
    pct: float          # signed
    from_price: float   # the window's high (drop) or low (pump)
    to_price: float
    took_min: float     # minutes since that high/low


class MoveDetector:
    """Flags a symbol when price is X% off its high/low within the last 5 min or 1 h."""

    WINDOWS = ((5, 300), (60, 3600))

    def __init__(self, tier_for: Callable[[str], Tier], cooldown_s: int = COOLDOWN_S):
        self.tier_for = tier_for
        self.cooldown_s = cooldown_s
        self.history: dict[str, deque] = {}
        self.last_alert: dict[str, tuple[float, float]] = {}  # symbol -> (ts, price)

    def update(self, ts: float, prices: dict[str, float]) -> list[Move]:
        moves = []
        for sym, price in prices.items():
            if not price or price <= 0:
                continue
            hist = self.history.setdefault(sym, deque())
            hist.append((ts, price))
            while hist and hist[0][0] < ts - 3600 - 120:
                hist.popleft()
            move = self._check(sym, ts, price, hist)
            if move and not self._cooling_down(sym, ts, price):
                self.last_alert[sym] = (ts, price)
                moves.append(move)
        return moves

    def change_pct(self, symbol: str, ts: float, window_s: int) -> float | None:
        """Point-to-point change over the window, for context ('market-wide?')."""
        hist = self.history.get(symbol)
        if not hist or ts - hist[0][0] < window_s * 0.8:
            return None
        ref = next(p for t, p in hist if t >= ts - window_s)
        return (hist[-1][1] / ref - 1) * 100

    def _check(self, sym: str, ts: float, price: float, hist: deque) -> Move | None:
        tier = self.tier_for(sym)
        for minutes, window_s in self.WINDOWS:
            threshold = tier.pct_5m if minutes == 5 else tier.pct_1h
            if ts - hist[0][0] < window_s * 0.8:
                continue  # not enough history yet (warm-up after start)
            pts = [(t, p) for t, p in hist if t >= ts - window_s]
            # On ties take the most recent extreme, so "took" measures the actual move.
            lo_t, lo = min(pts, key=lambda x: (x[1], -x[0]))
            hi_t, hi = max(pts, key=lambda x: (x[1], x[0]))
            up, down = (price / lo - 1) * 100, (price / hi - 1) * 100
            pct, ref, ref_t = (up, lo, lo_t) if up >= -down else (down, hi, hi_t)
            if abs(pct) >= threshold:
                return Move(sym, minutes, pct, ref, price, (ts - ref_t) / 60)
        return None

    def _cooling_down(self, sym: str, ts: float, price: float) -> bool:
        """Quiet for 30 min after an alert, unless price moved another full 5-min step since."""
        if sym not in self.last_alert:
            return False
        last_ts, last_price = self.last_alert[sym]
        if ts - last_ts >= self.cooldown_s:
            return False
        return abs(price / last_price - 1) * 100 < self.tier_for(sym).pct_5m


class LiquidityWatch:
    """Memecoin rug / LP-pull warning: liquidity down >30% from its 1-hour high."""

    def __init__(self, drop_pct: float = 30.0, cooldown_s: int = 2 * 3600):
        self.drop_pct, self.cooldown_s = drop_pct, cooldown_s
        self.history: dict[str, deque] = {}
        self.last_alert: dict[str, float] = {}

    def update(self, ts: float, symbol: str, liquidity: float | None) -> tuple[float, float] | None:
        if not liquidity:
            return None
        hist = self.history.setdefault(symbol, deque())
        hist.append((ts, liquidity))
        while hist and hist[0][0] < ts - 3600:
            hist.popleft()
        peak = max(v for _, v in hist)
        drop = (1 - liquidity / peak) * 100
        if drop >= self.drop_pct and ts - self.last_alert.get(symbol, float("-inf")) >= self.cooldown_s:
            self.last_alert[symbol] = ts
            return peak, liquidity
        return None


class Liquidations:
    """Rolling liquidation totals from the Binance forceOrder stream."""

    def __init__(self):
        self.events: deque = deque()  # (ts, usd, side, symbol); side SELL = a long was liquidated
        self.lock = threading.Lock()  # written by the websocket thread, read by the main loop

    def add(self, ts: float, usd: float, side: str, symbol: str) -> None:
        with self.lock:
            self.events.append((ts, usd, side, symbol))
            while self.events and self.events[0][0] < ts - 3600:
                self.events.popleft()

    def totals(self, now: float, window_s: int = 300) -> dict:
        longs = shorts = 0.0
        by_symbol: dict[str, float] = {}
        with self.lock:
            events = list(self.events)
        for ts, usd, side, sym in events:
            if ts < now - window_s:
                continue
            if side == "SELL":
                longs += usd
            else:
                shorts += usd
            by_symbol[sym] = by_symbol.get(sym, 0.0) + usd
        top = sorted(by_symbol.items(), key=lambda kv: -kv[1])[:3]
        return {"longs": longs, "shorts": shorts, "total": longs + shorts, "top": top}


class Threshold:
    """Fires when a value crosses a limit, then stays quiet for the cooldown."""

    def __init__(self, cooldown_s: int = COOLDOWN_S):
        self.cooldown_s = cooldown_s
        self.last = float("-inf")

    def fire(self, now: float, triggered: bool) -> bool:
        if triggered and now - self.last >= self.cooldown_s:
            self.last = now
            return True
        return False


class SeriesChange:
    """Change of a sampled value (e.g. BTC open interest) over a window."""

    def __init__(self, keep_s: int = 3600):
        self.keep_s = keep_s
        self.points: deque = deque()

    def add(self, ts: float, value: float) -> None:
        self.points.append((ts, value))
        while self.points and self.points[0][0] < ts - self.keep_s:
            self.points.popleft()

    def change_pct(self, now: float, window_s: int) -> float | None:
        if not self.points or now - self.points[0][0] < window_s * 0.8:
            return None
        ref = next(v for t, v in self.points if t >= now - window_s)
        return (self.points[-1][1] / ref - 1) * 100 if ref else None


def due_reminders(events: list[dict], now: datetime, sent: set[str]) -> tuple[list[list[dict]], list[list[dict]]]:
    """High-impact events grouped by release time.

    Returns (upcoming groups due a 30-min warning, released groups due a reaction note).
    Marks returned groups in `sent`, so each fires once.
    """
    groups: dict[datetime, list[dict]] = {}
    for e in events:
        if e["impact"] == "High":
            groups.setdefault(e["time_kst"], []).append(e)
    pre, post = [], []
    for when, group in sorted(groups.items()):
        key = when.astimezone(timezone.utc).isoformat()
        until = when - now
        if timedelta(0) < until <= timedelta(minutes=30) and f"pre:{key}" not in sent:
            sent.add(f"pre:{key}")
            pre.append(group)
        elif timedelta(minutes=3) <= -until <= timedelta(minutes=15) and f"post:{key}" not in sent:
            sent.add(f"post:{key}")
            post.append(group)
    return pre, post
