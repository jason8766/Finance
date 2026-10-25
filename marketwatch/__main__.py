"""CLI entry point.

    python -m marketwatch brief             build and send the daily brief
    python -m marketwatch brief --dry-run   print it instead of sending
    python -m marketwatch brief --no-llm    skip Claude (headlines only)
    python -m marketwatch check             verify every data source and secret
"""

import argparse
import logging
import sys

from . import brief, telegram
from .config import load_settings
from .sources import binance, dex, econ_calendar, news, sentiment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marketwatch")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_brief = sub.add_parser("brief", help="build and send the daily brief")
    p_brief.add_argument("--dry-run", action="store_true", help="print instead of sending")
    p_brief.add_argument("--no-llm", action="store_true", help="skip Claude commentary")
    sub.add_parser("check", help="verify data sources, watchlist and secrets")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()

    if args.cmd == "brief":
        text = brief.build(settings, use_llm=not args.no_llm)
        if args.dry_run:
            print(text)
            return 0
        if not (settings.telegram_token and settings.telegram_chat_id):
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env", file=sys.stderr)
            return 1
        telegram.send(settings.telegram_token, settings.telegram_chat_id, text)
        logging.info("brief sent (%d chars)", len(text))
        return 0

    if args.cmd == "check":
        return check(settings)
    return 1


def check(settings) -> int:
    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            print(f"✅ {name}: {fn()}")
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            ok = False
            print(f"❌ {name}: {type(e).__name__}: {e}")

    def market():
        m = binance.fetch_market(settings.watch_perps)
        found = ", ".join(r["symbol"] for r in m["watchlist"])
        msg = f"top20 = {', '.join(r['symbol'] for r in m['top20'])}\n   watchlist = {found}"
        if m["watchlist_missing"]:
            msg += f"\n   ⚠️  NOT FOUND on Binance futures: {', '.join(m['watchlist_missing'])}"
        return msg

    def memes():
        rows = dex.fetch_tokens(settings.memecoins)
        return ", ".join(f"{r['symbol']}={'ok' if r['found'] else 'NOT FOUND'}" for r in rows)

    step("Binance futures", market)
    step("DexScreener", memes)
    step("Fear & Greed", lambda: sentiment.fear_greed())
    step("CoinGecko global", lambda: f"BTC dom {sentiment.global_market()['btc_dominance_pct']:.1f}%")
    step("Economic calendar", lambda: f"{len(econ_calendar.fetch_events())} events")
    step("News feeds", lambda: f"{len(news.fetch_headlines(settings.news_feeds, news.last_24h()))} headlines in last 24h")
    step("Telegram bot", lambda: "@" + telegram.get_me(settings.telegram_token)["username"])
    print(("✅" if settings.telegram_chat_id else "❌") + " TELEGRAM_CHAT_ID set")
    print(("✅" if settings.anthropic_api_key else "⚠️ ") + " ANTHROPIC_API_KEY "
          + ("set" if settings.anthropic_api_key else "not set (brief works, without AI commentary)"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
