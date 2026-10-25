# Crypto Market Watch — Spec

Personal market-awareness system for a Binance futures + Solana memecoin trader.
**Goal:** never be surprised by a market-moving event, scheduled or not.
It informs and explains; it does **not** give trade signals.

- Language: English
- Delivery: Telegram bot (private chat)
- Timezone for all output: KST (UTC+9)

## Features

### A. Daily Brief — 09:00 KST (00:00 UTC, Binance daily close + funding)

1. **Market snapshot** — BTC / ETH / SOL price + 24h %, BTC dominance, total OI,
   funding (majors + extremes), 24h liquidations (long vs short), Fear & Greed.
2. **Yesterday in 5 bullets** — top macro / political / regulatory / crypto news,
   each with a one-line "why it matters".
3. **Today (KST)** — US data (CPI, PPI, NFP, PCE, jobless claims, GDP, ISM, retail
   sales), FOMC / Fed speakers, BOJ / ECB / PBOC, token unlocks, Binance
   listings / delistings, ETF flows from the prior US session.
4. **Week ahead** — the 3–5 highest-volatility events.
5. **Risk flags** — extreme funding, OI build-up, depegs, exchange incidents,
   war / tariff headlines.
6. **Watchlist corner** — 24h move of each watchlist coin and memecoin.

### B. Event Reminders

- T-30 min: "CPI in 30 min — forecast 2.9%, previous 3.0%".
- T+1–2 min: actual vs forecast, beat/miss, and BTC reaction so far.
- Covered: high-impact USD events, FOMC rate decision + press conference,
  BOJ / ECB decisions. Others appear in the brief only.

### C. Move Alerts — 24/7, medium sensitivity

Rule-based detection (cheap, instant); the LLM only runs after a trigger fires.

| Universe | 5-min move | 1-hour move |
|---|---|---|
| BTC | ±1.5% | ±3% |
| ETH, SOL | ±2% | ±4% |
| Other top-20 + watchlist perps | ±3% | ±6% |
| Solana memecoins (DEX) | ±10% | ±20% |

Additional triggers:
- Liquidations > $50M across Binance perps in 5 min
- BTC open interest change > 3% in 15 min
- Funding rate beyond ±0.1% / 8h on a watched symbol
- Memecoin: liquidity drop > 30% (rug / LP pull warning) or 5× volume spike

Noise control:
- 30-min cooldown per symbol; re-alert only if the move extends by another full
  threshold step.
- A market-wide move (≥ 5 of top-20 moving together) is merged into **one** alert.

Alert content:
```
🔴 BTC −2.8% in 12m ($67.2k → $65.3k)
Liquidations: $180M longs / $12M shorts
OI −4.1% · Funding 0.012% → 0.004%
Scope: market-wide (18/20 down) · NQ −0.9%, DXY +0.4%
Likely cause: hotter-than-expected PPI (21:30 KST, 0.5% vs 0.2% exp.)
```
Honesty rule: if no catalyst is found, say so ("no news found — looks
liquidation / flow-driven"). Never invent a reason.

## Universe

- **Top 20** Binance USDⓈ-M perpetuals by 24h quote volume, stablecoins excluded,
  refreshed daily at brief time.
- **Watchlist** (user-edited, `config/config.yaml`):
  - Perps: BTC, ETH, ZEC, XRP, SNDK, KORU, HYPE, SOXL, SKHY, PUMP, XAU, CRCL, ONDO,
    FARTCOIN, JUP, UNITREE, KMNO, SKR (resolved against Binance's live contract list;
    `python -m marketwatch check` reports any that don't match)
  - Solana memecoins (tracked by mint address, never by ticker — tickers collide):
    - FARTCOIN — `9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump` (also has a Binance perp)
    - KIMCHI — `CSocnzcRpgSCr1naKcNTieWZUxb6rABe45j844m7pump`
    - NEET — `Ce2gx9KGXJ6C9Mp5b5x1sn9Mg87JwEbrQby4Zqo3pump`

## Architecture

Single Python (asyncio) process, SQLite for state.

```
collectors/
  binance_ws.py    prices (1m klines), liquidations (forceOrder), mark price/funding
  binance_rest.py  open interest, top-20 universe refresh
  dex.py           DexScreener polling for Solana memecoins (30–60 s)
  news.py          RSS + news API headlines (CoinDesk, The Block, Decrypt, Reuters, CryptoPanic)
  calendar.py      economic calendar (forecast / previous / actual)
  macro.py         NQ / DXY / US10Y snapshots for context
detectors/         threshold + cooldown logic → Alert objects
analyst/           Claude API: writes brief, explains alerts (small model for alerts)
notifier/          Telegram Bot API
scheduler          09:00 KST brief, event reminders
```

## Hosting

Constraints: must run 24/7 (websockets), and **Binance blocks US IPs and many
datacenter ranges** (HTTP 451) — so US-based free CI (e.g. GitHub Actions) and
US-only free tiers can't reach the futures API.

| Option | Cost | Notes |
|---|---|---|
| **Oracle Cloud Always Free** (home region Seoul / Chuncheon / Tokyo) | $0 | **Chosen.** Setup: [ORACLE_SETUP.md](ORACLE_SETUP.md). Asian region avoids the US block. Signup needs a card; ARM capacity in Asian regions can be scarce — the free AMD micro VM (1 GB) is enough for this workload. Home region can't be changed later. |
| Home PC / Raspberry Pi in Korea | $0 (+electricity) | Korean IP works with Binance. Uptime depends on your machine and internet. |
| Small VPS in Tokyo / Singapore | ~$4–6 / mo | Fallback if Oracle fails. Verify Binance reachability before committing. |
| Fly.io / Railway / Render / Koyeb | — | No free always-on worker anymore; some regions blocked by Binance. |

Plus: Claude API usage. The brief makes one call per day (~15-25K input tokens):
roughly $3-5/month on `claude-opus-5-5`, about $0.10/month on `claude-haiku-5-5`
(set `llm_model` in `config/config.yaml`). Without an API key the brief still
runs, showing raw headlines instead of commentary.

## Phases

1. Daily Brief + calendar → Telegram
2. Event Reminders (pre-event + actual vs forecast)
3. Move Alerts (rules only) — CEX perps
4. LLM "why" analysis on alerts
5. Solana memecoin monitoring (DEX)
6. Later: positions via read-only Binance key ("this affects your ETH long")
