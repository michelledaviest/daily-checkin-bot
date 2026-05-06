# Daily Check-in Bot

Host your own accountability buddy as a voice-based check-in bot on Telegram! The bot nudges you on Telegram twice a day, takes a spoken (or typed) reply, asks structured follow-ups when something's missing, and logs the results to a Google Sheet. Built to run as a `systemd` service on a Google Compute Engine `e2-micro` free-tier VM at **$0/month**. All the data is saved in a Google Sheet on your account. Feel free to fork and customize with your own metrics or setup! All the setup instructions are in the README to get this version up and running.

You name the bot in your `.env` (default suggestion: a friendly first name like "June"). Conversations look like this:

> **Bot (9 AM):** ☀️ Good morning! Reminders for today: drink water, work from your desk, no couch. Send a voice note with how many hours you slept.
>
> **You:** *"7 and a half hours"*
>
> **Bot:** logged ✓ slept 7.5h
>
> **Bot (7 PM):** 🌙 Evening check-in. Send a voice note covering: water, mood, body, shoulder pain, neck spasms, migraine, exercise, steps, and desk vs. couch/bed hours.
>
> **You:** *"Drank about 50 oz, mood is a 7, shoulder pain a 3, no migraine, no neck spasms, did a 45 min spin class, around 8000 steps, worked 6 hours at desk and 1 on couch."*
>
> **Bot:** Got most of it. Still need:
> • body notes (one line on how you feel)
>
> **You:** *"Body feels fine, slightly stiff in the upper back"*
>
> **Bot:** logged ✓ 50 oz, mood 7, shoulder 3, no migraine, 45m spin, 8000 steps

One row per day in the sheet. Morning fills `sleep_hours`; evening fills the rest.

## What it tracks (default schema)

| Field | Filled by | Type |
|---|---|---|
| `sleep_hours` | morning | number |
| `water_oz` | evening | number |
| `mood_score` | evening | 1–10 |
| `body_notes` | evening | free-text |
| `desk_hours` | evening | hours at proper desk |
| `couch_bed_hours` | evening | hours from couch/bed |
| `shoulder_pain` | evening | 0–10 |
| `neck_spasms` | evening | bool |
| `migraine` | evening | bool |
| `migraine_severity` | evening | 0–10 |
| `exercise_type` | evening | enum (none / strength / swim / spin / hike / run / yoga / other) |
| `exercise_minutes` | evening | number |
| `steps` | evening | int |

Plus `morning_logged_at`, `evening_logged_at`, transcripts, turn counts.

The schema is defined in **`src/prompts.py`** (`Fields` Pydantic model + `REQUIRED_FIELDS_*`) and **`src/sheets.py`** (`COLUMN_ORDER`). If you want to track different things — sleep quality, anxiety, productivity, whatever — edit those two files and update `docs/sheet_header.txt` to match.

## Architecture

- **Telegram bot** (long-polling, no public URL needed)
- **Gemini 2.5 Flash** (free tier on Google AI Studio) — accepts audio + text natively, returns structured JSON via Pydantic `response_schema`. Multi-turn: the model sees the full audio history each turn.
- **Google Sheets** — one row per day, upserted by `local_date`
- **APScheduler** — fires the morning and evening nudges in your configured timezone (DST-safe via `zoneinfo`)
- **healthchecks.io** — emails you if the bot crashes or you miss check-ins
- Runs as `systemd` on a free-tier GCE `e2-micro` VM. ~$0/month.

## Prerequisites

You'll need accounts for:
1. **Telegram** (free)
2. **Google account** — used for AI Studio (Gemini) and Google Cloud (Sheets API + GCE)
3. **healthchecks.io** (free)

Plus, locally: Python 3.10+ and the `gcloud` CLI.

## Setup

### 1. Telegram bot

In Telegram, message **@BotFather**:
- `/newbot` → pick a display name (e.g. "Otto") → pick a username ending in `bot` (e.g. `your_checkin_bot`)
- Save the token BotFather gives you.
- Search for your bot, send any message to it.
- Get your numeric chat ID:
  ```bash
  curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
  ```
  Look for `"chat":{"id":<number>}` in the JSON.

### 2. Gemini API key

Visit https://aistudio.google.com/apikey, click **Create API key** (use the default project), copy the key.

### 3. Google Cloud project + service account

1. Create a Google Cloud project at https://console.cloud.google.com (e.g. `daily-checkin-bot`). Link a billing account — actual cost stays $0 within free-tier limits, but billing is required.
2. Enable the **Google Sheets API** (APIs & Services → Library).
3. Create a service account (IAM & Admin → Service Accounts). No project roles needed.
4. On the service account, **Keys** → **Add Key** → JSON. Download. Save as `gsa-key.json` in the repo root (gitignored).

### 4. Google Sheet

1. Create a new Google Sheet, rename the first tab to `checkins` (lowercase, exact).
2. Copy the contents of `docs/sheet_header.txt` and paste into cell A1 — Sheets will spread across A1:T1 (20 columns).
3. Click **Share**, paste the service-account email (looks like `name@project.iam.gserviceaccount.com`), set role to **Editor**, uncheck "Notify people". This is the step everyone forgets.
4. The sheet ID is in the URL between `/d/` and `/edit`.

### 5. healthchecks.io

Create a free account, add two checks:
- **bot-heartbeat** — schedule "every 1 hour", grace 15 min. Bot pings every 30 min.
- **daily-checkins** — schedule "every day", grace 4 hours. Bot pings at 23:55 if any check-in was logged.

Copy each check's ping URL.

### 6. Local `.env`

```bash
git clone https://github.com/<your-username>/daily-checkin-bot.git
cd daily-checkin-bot
cp .env.example .env
```

Fill in `.env`:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_ID=...     # restricts the bot to only respond to your chat
GEMINI_API_KEY=...
GSA_KEY_PATH=./gsa-key.json       # for local dev; will change for VM
SHEET_ID=...
HC_HEARTBEAT_URL=...
HC_DAILY_URL=...
BOT_NAME=Otto                     # whatever name you picked in BotFather
```

`MORNING_HOUR`, `EVENING_HOUR`, `TIMEZONE` are also configurable.

### 7. Local test run

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m src.main
```

In Telegram, send `/start`, then `/now morning` to trigger an immediate check-in. Reply with a voice note or text. Confirm the row lands in your sheet.

### 8. Deploy to GCE

Create a free-tier `e2-micro` VM:

```bash
gcloud config set project <YOUR_PROJECT_ID>
gcloud services enable compute.googleapis.com
gcloud compute instances create checkin-bot \
  --zone=us-east1-b \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard
```

(`us-east1-b`, `us-central1-*`, and `us-west1-*` are eligible for the free tier. Pick the region closest to you.)

Set a $1 monthly budget alert in the Cloud Console as a safety tripwire.

SSH in and prep:

```bash
gcloud compute ssh checkin-bot --zone=us-east1-b
# On the VM:
sudo apt update && sudo apt install -y python3-venv python3-pip
sudo mkdir -p /opt/checkin /var/log/checkin
sudo chown $USER:$USER /opt/checkin /var/log/checkin
exit
```

Package the code on your laptop and copy it over:

```bash
tar czf /tmp/checkin.tar.gz \
  --exclude='.venv' --exclude='.env' --exclude='gsa-key.json' \
  --exclude='logs' --exclude='state' --exclude='__pycache__' \
  --exclude='.git' \
  src deploy docs scripts pyproject.toml README.md LICENSE .env.example .gitignore

gcloud compute scp /tmp/checkin.tar.gz checkin-bot:/tmp/ --zone=us-east1-b
gcloud compute scp .env checkin-bot:/tmp/.env --zone=us-east1-b
gcloud compute scp gsa-key.json checkin-bot:/tmp/gsa-key.json --zone=us-east1-b
```

SSH back in and finish:

```bash
gcloud compute ssh checkin-bot --zone=us-east1-b
# On the VM:
cd /opt/checkin
tar xzf /tmp/checkin.tar.gz
mv /tmp/.env .env
mv /tmp/gsa-key.json gsa-key.json
chmod 600 .env gsa-key.json
sed -i 's|^GSA_KEY_PATH=.*|GSA_KEY_PATH=/opt/checkin/gsa-key.json|' .env
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# systemd
sed -i "s|^User=.*|User=$(whoami)|" deploy/checkin-bot.service
sudo cp deploy/checkin-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now checkin-bot
sudo systemctl status checkin-bot
```

If the status shows `active (running)`, you're done. Logs via `journalctl -u checkin-bot -f`.

## Usage

In Telegram:
- `/start` — intro
- `/now morning` or `/now evening` — manually start a check-in
- `/cancel` — abort current check-in
- `/status` — see what's mid-flight

Reply with voice notes or typed text — the Gemini call accepts both. Mix freely (voice the long answer, type the quick correction).

## Customization points

- **Bot name** — `BOT_NAME` env var.
- **Schedule** — `MORNING_HOUR`, `EVENING_HOUR`, `TIMEZONE` env vars.
- **Metrics** — edit `src/prompts.py` (`Fields` model + `REQUIRED_FIELDS_*` lists + system prompt) and `src/sheets.py` (`COLUMN_ORDER`). Then update `docs/sheet_header.txt` and the header row in your sheet.
- **Voice/persona** — edit `SYSTEM_PROMPT` in `src/prompts.py`.

## Cost

| Component | Free tier covers |
|---|---|
| Telegram Bot API | unlimited |
| Gemini 2.5 Flash | ~1500 requests/day (you'll use ~10) |
| Google Sheets API | far more than 2 writes/day |
| healthchecks.io | 20 checks free (you use 2) |
| GCE `e2-micro` | 1 instance forever-free in `us-east1`/`us-central1`/`us-west1` |
| Disk | 30 GB standard PD covered by free tier |

Set a $1/month budget alert as a tripwire and you'll catch any drift immediately.

## License

MIT — see `LICENSE`.
