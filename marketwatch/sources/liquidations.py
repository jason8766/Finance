"""Binance USDⓈ-M liquidation stream (all symbols), run in a background thread.

Binance pushes at most one liquidation per symbol per second on this stream,
so totals are a lower bound — good for spotting cascades, not for exact sums.
"""

import json
import logging
import threading
import time

from websockets.sync.client import connect

from ..alerts import Liquidations

log = logging.getLogger(__name__)

WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"


class LiquidationStream(threading.Thread):
    def __init__(self, sink: Liquidations, url: str = WS_URL):
        super().__init__(name="liquidations", daemon=True)
        self.sink, self.url = sink, url
        self.last_message = 0.0

    def run(self) -> None:
        backoff = 1
        while True:
            try:
                with connect(self.url, open_timeout=20, close_timeout=5) as ws:
                    log.info("liquidation stream connected")
                    backoff = 1
                    for raw in ws:
                        self.last_message = time.time()
                        self.handle(raw)
            except Exception as e:  # noqa: BLE001 - reconnect on anything (Binance drops streams every 24h)
                log.warning("liquidation stream error: %s; reconnecting in %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def handle(self, raw: str | bytes) -> None:
        try:
            order = json.loads(raw)["o"]
            usd = float(order["ap"]) * float(order["z"])
        except (ValueError, KeyError, TypeError):
            return
        self.sink.add(time.time(), usd, order.get("S", ""), order.get("s", ""))


def probe(url: str = WS_URL, timeout: float = 15) -> str:
    """Connectivity check for `marketwatch check`."""
    with connect(url, open_timeout=timeout, close_timeout=5):
        return "connected"
