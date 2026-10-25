# Crypto Market Watch

Personal market-awareness bot for Binance futures and Solana memecoins, delivered on Telegram.

- **Daily Brief** at 09:00 KST: market snapshot, last 24h news, today's macro calendar
  (CPI, PPI, FOMC, NFP, ...), week ahead, risk flags, watchlist and memecoins.
- Coming next: event reminders, 24/7 move alerts with "why it moved" analysis.

Docs:
- [docs/SPEC.md](docs/SPEC.md): goals, features, architecture, phases
- [docs/ORACLE_SETUP.md](docs/ORACLE_SETUP.md): free server setup
- [docs/DEPLOY.md](docs/DEPLOY.md): install and schedule the brief

```bash
python -m marketwatch check               # verify data sources and secrets
python -m marketwatch brief --dry-run     # print today's brief
python -m marketwatch brief               # send it to Telegram
```

Configuration: `config/config.yaml` (watchlist, memecoins, feeds, model).
Secrets: `~/marketwatch-secrets/.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`).

Tests: `pip install -r requirements-dev.txt && pytest`
