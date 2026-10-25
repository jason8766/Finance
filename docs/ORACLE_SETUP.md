# Oracle Cloud Setup Guide

Sets up a free, always-on Ubuntu server in Asia to run Crypto Market Watch.
Takes about 30–45 minutes. Oracle's console layout changes from time to time;
if a button name differs, look for the closest equivalent.

What you'll end up with:
- An Always Free Ubuntu VM in Seoul / Chuncheon / Tokyo
- SSH access from your computer
- Confirmed connectivity to Binance, Telegram and DexScreener
- A Telegram bot token + chat ID stored safely on the server

---

## 1. Create the Oracle Cloud account

1. Go to <https://www.oracle.com/cloud/free/> → **Start for free**.
2. Fill in your details. Use your real name and address — mismatches with the
   card are the most common reason signups get rejected.
3. **Home Region — choose carefully, it can never be changed.** Free VMs can only
   be created in the home region. Pick one of:
   - **South Korea Central (Seoul)** — recommended
   - **South Korea North (Chuncheon)**
   - **Japan East (Tokyo)** — closest to Binance's servers
   Do **not** pick a US region: Binance blocks US IPs.
4. Card verification: Oracle places a small temporary hold and releases it. You
   are not charged while you stay within Always Free resources.
5. Wait for the "Your account is ready" email (minutes to a few hours), then sign
   in at <https://cloud.oracle.com>.

## 2. (Strongly recommended) Upgrade to Pay As You Go + set a budget alert

Oracle may **reclaim idle Always Free VMs** (very low CPU/network/memory use over
7 days). Our bot is lightweight, so it could look "idle". Pay As You Go accounts
are not subject to this, and Always Free resources stay free.

1. Console menu → **Billing & Cost Management** → **Upgrade and Manage Payment**
   → **Upgrade to Pay As You Go**.
2. Then **Billing & Cost Management → Budgets → Create Budget**:
   - Amount: `1` (USD)
   - Alert threshold: 1% of budget (actual spend)
   - Your email
   This emails you if anything ever starts costing money.
3. Rule from now on: only create resources marked **"Always Free-eligible"**.

You can skip this step and do it later if the VM ever gets stopped.

## 3. Create the VM

Console menu → **Compute → Instances → Create instance**.

| Setting | Value |
|---|---|
| Name | `marketwatch` |
| Image | **Canonical Ubuntu 24.04** (click *Change image*) |
| Shape | **Ampere → VM.Standard.A1.Flex**, 1 OCPU, 6 GB RAM (plenty). If unavailable, use **AMD → VM.Standard.E2.1.Micro** (1 GB RAM — also enough). Both must show *Always Free-eligible*. |
| Networking | *Create new virtual cloud network* + *Create new public subnet* (defaults) |
| Public IPv4 | **Assign a public IPv4 address** = Yes |
| SSH keys | **Generate a key pair for me** → click **Save private key** (and save public key too). Keep this file safe; you can't download it again. |
| Boot volume | Default (~47 GB, within the free 200 GB) |

Click **Create**. After 1–2 minutes the status turns **Running**. Copy the
**Public IP address** from the instance page.

### "Out of capacity" error?

Common for ARM in Asian regions. Try, in order:
1. Change **Availability domain** (if your region has more than one) and retry.
2. Use the **VM.Standard.E2.1.Micro** (AMD) shape instead — usually available.
3. Retry ARM later (early morning KST often works).

You do **not** need to open any inbound ports. The bot only makes outbound
connections (Binance, Telegram, news), so the default firewall (SSH only) is
ideal.

## 4. Connect with SSH

Move the downloaded private key somewhere permanent, e.g. `~/.ssh/oracle_marketwatch.key`.

**macOS / Linux**
```bash
chmod 600 ~/.ssh/oracle_marketwatch.key
ssh -i ~/.ssh/oracle_marketwatch.key ubuntu@<PUBLIC_IP>
```

**Windows (PowerShell)**
```powershell
# Restrict key permissions (required by ssh)
icacls $HOME\.ssh\oracle_marketwatch.key /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i $HOME\.ssh\oracle_marketwatch.key ubuntu@<PUBLIC_IP>
```

Type `yes` on the first connection. You should see an `ubuntu@marketwatch:~$` prompt.

Optional shortcut — add to `~/.ssh/config` so you can just type `ssh marketwatch`:
```
Host marketwatch
    HostName <PUBLIC_IP>
    User ubuntu
    IdentityFile ~/.ssh/oracle_marketwatch.key
```

## 5. Base server setup

Run on the server:

```bash
# Updates + tools
sudo apt update && sudo apt -y upgrade
sudo apt -y install git python3 python3-venv python3-pip unattended-upgrades sqlite3

# Automatic security updates
sudo dpkg-reconfigure -plow unattended-upgrades   # choose "Yes"

# Keep the system clock in UTC (the app converts to KST itself)
timedatectl                                        # should show "System clock synchronized: yes"
```

**Only if you used the 1 GB AMD Micro shape**, add swap so installs don't run out of memory:
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Reboot if the upgrade asked for it: `sudo reboot`, wait a minute, reconnect.

## 6. Verify connectivity (the important check)

```bash
curl -s https://fapi.binance.com/fapi/v1/ping; echo        # expect: {}
curl -s "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT" | head -c 200; echo
curl -s -o /dev/null -w "telegram %{http_code}\n" https://api.telegram.org         # expect 200 or 302
curl -s -o /dev/null -w "dexscreener %{http_code}\n" "https://api.dexscreener.com/latest/dex/tokens/9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"  # expect 200
```

- `{}` and a JSON blob with `markPrice` → Binance works. 🎉
- `451` or "restricted location" → Binance blocks this IP. Tell me; fallback
  options are a small Tokyo/Singapore VPS or running on a home machine.

## 7. Create the Telegram bot

On your phone/desktop Telegram:

1. Open a chat with **@BotFather** → send `/newbot`.
2. Give it a name (e.g. `Market Watch`) and a username ending in `bot`
   (e.g. `jason_marketwatch_bot`).
3. BotFather replies with a **token** like `123456789:AA...`. Treat it like a
   password. **Don't paste it into chats (including this one) or commit it to git.**
4. Open your new bot and send it any message (e.g. `hi`) — this is required
   before it can message you.

Get your chat ID on the server:
```bash
read -s -p "Bot token: " TG_TOKEN; echo
curl -s "https://api.telegram.org/bot${TG_TOKEN}/getUpdates" | grep -o '"chat":{"id":[0-9-]*' | head -1
```
The number after `"id":` is your **chat ID**.

Send yourself a test message:
```bash
read -p "Chat ID: " TG_CHAT; curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
  -d chat_id="${TG_CHAT}" -d text="✅ Market Watch server is online" > /dev/null && echo sent
```

## 8. Store secrets on the server

```bash
mkdir -p ~/marketwatch-secrets && chmod 700 ~/marketwatch-secrets
nano ~/marketwatch-secrets/.env
```
Contents:
```
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_CHAT_ID=123456789
ANTHROPIC_API_KEY=            # add later, from https://console.anthropic.com
```
Save (Ctrl+O, Enter, Ctrl+X), then lock it down:
```bash
chmod 600 ~/marketwatch-secrets/.env
```

## 9. Give the server read access to this repo

If the GitHub repo is private, use a read-only **deploy key**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N "" -C "marketwatch-server"
cat ~/.ssh/github_deploy.pub
```
GitHub → repo **Settings → Deploy keys → Add deploy key** → paste, leave
*Allow write access* **unchecked**.

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/github_deploy
    IdentitiesOnly yes
EOF
ssh -T git@github.com            # type yes; expect "successfully authenticated"
git clone git@github.com:jason8766/Finance.git ~/Finance
```

(Public repo? Just `git clone https://github.com/jason8766/Finance.git ~/Finance`.)

## 10. Install and schedule the daily brief

See [DEPLOY.md](DEPLOY.md).

---

## Checklist

- [ ] Account created, home region in Korea/Japan
- [ ] (Recommended) Pay As You Go + $1 budget alert
- [ ] VM running, SSH works
- [ ] Step 6: Binance returns `{}`
- [ ] Telegram test message received
- [ ] `.env` saved with `chmod 600`
- [ ] Repo cloned on the server

Once Step 6 and the Telegram test pass, report back (just "Binance OK, Telegram
OK" — no tokens) and we'll start Phase 1.
