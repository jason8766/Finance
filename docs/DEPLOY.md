# Deploying the Daily Brief

Run these on the Oracle server (`ssh -i ... ubuntu@<server-ip>`), after finishing
[ORACLE_SETUP.md](ORACLE_SETUP.md) steps 1–8.

## 1. Get the code

```bash
cd ~
git clone https://github.com/jason8766/Finance.git    # private repo: see ORACLE_SETUP.md step 9
cd ~/Finance
git checkout claude/sharp-cerf-gpgy2h                 # until this branch is merged into main
```

## 2. Install

```bash
cd ~/Finance
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Check every source

```bash
.venv/bin/python -m marketwatch check
```

Every line should be ✅. Watch for:
- `NOT FOUND on Binance futures: ...` — a watchlist name doesn't match a live
  Binance perp. Fix the name in `config/config.yaml` (use the base asset as shown
  on Binance, e.g. `PEPE` matches `1000PEPEUSDT` automatically).
- `ANTHROPIC_API_KEY not set` — fine for now; the brief shows raw headlines
  instead of commentary until you add a key (step 6).

## 4. Preview, then send one for real

```bash
.venv/bin/python -m marketwatch brief --no-llm --dry-run   # print to the terminal
.venv/bin/python -m marketwatch brief --no-llm             # send to Telegram
```

## 5. Schedule it for 09:00 KST every day

```bash
sudo cp deploy/marketwatch-brief.service deploy/marketwatch-brief.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now marketwatch-brief.timer
systemctl list-timers marketwatch-brief.timer     # NEXT should be 00:00:30 UTC (09:00 KST)
```

Useful commands:
```bash
sudo systemctl start marketwatch-brief.service    # send a brief right now
journalctl -u marketwatch-brief -n 50             # logs of the last runs
```

## 6. Start the 24/7 monitor (move alerts + event reminders)

```bash
cd ~/Finance && .venv/bin/pip install -r requirements.txt   # adds the websocket library
sudo cp deploy/marketwatch-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now marketwatch-monitor.service
systemctl status marketwatch-monitor --no-pager             # should say "active (running)"
journalctl -u marketwatch-monitor -f                        # live logs (Ctrl+C to exit)
```
You get a "🟢 Monitor started" message in Telegram. Price alerts start after a
5-minute warm-up (1-hour alerts after ~50 minutes). systemd restarts it if it
crashes and starts it again after a reboot.

After a `git pull`, restart it to load new code:
```bash
sudo systemctl restart marketwatch-monitor
```

## 7. Turn on AI commentary (optional, paid)

1. Create a key at <https://console.anthropic.com> → **API Keys**, and add a
   few dollars of credit under **Billing**. Set a monthly limit there too.
2. On the server: `nano ~/marketwatch-secrets/.env` and fill in
   `ANTHROPIC_API_KEY=sk-ant-...`
3. Test: `.venv/bin/python -m marketwatch brief --dry-run`

Model and cost are set by `llm_model` in `config/config.yaml`:
`claude-opus-5-5` (best writing, ~$3–5/month) or `claude-haiku-5-5` (~$0.10/month).

## Updating later

```bash
cd ~/Finance && git pull && .venv/bin/pip install -r requirements.txt
```

If the repo is private, `git pull` over HTTPS fails. Add a read-only deploy key
(ORACLE_SETUP.md step 9), then switch the server's copy to SSH once:
```bash
cd ~/Finance && git remote set-url origin git@github.com:jason8766/Finance.git
git pull
```
The brief timer picks up new code on its next run; restart the monitor
(`sudo systemctl restart marketwatch-monitor`).
