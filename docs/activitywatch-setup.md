# Setting up laptop screen time tracking

The bot can automatically log your daily laptop screen time — active hours, longest unbroken focus block, and break count — without you lifting a finger. It uses [ActivityWatch](https://activitywatch.net), a free, open-source, fully local time tracker that runs in the background on your laptop.

This is **opt-in**. The bot works fine without it; these columns just stay blank in your sheet.

---

## What you'll need

- The repo cloned on your **laptop** (not the server — the script runs locally)
- Python 3.10+ on the laptop
- The same Google service-account JSON key you use for the bot (or a copy of it)
- The same Google Sheet ID

---

## Step 1 — Install ActivityWatch

Download the latest release for your OS from [activitywatch.net](https://activitywatch.net) and unzip it somewhere permanent (e.g. `~/tools/activitywatch`).

Start it once manually to confirm it works:

```bash
~/tools/activitywatch/aw-server &
```

Open `http://localhost:5600` in a browser — you should see the ActivityWatch dashboard.

**Make ActivityWatch start automatically on login:**

- **Linux:** Create `~/.config/autostart/activitywatch.desktop`:
  ```ini
  [Desktop Entry]
  Type=Application
  Name=ActivityWatch
  Exec=/home/YOUR_USER/tools/activitywatch/aw-server
  Hidden=false
  X-GNOME-Autostart-enabled=true
  ```
- **macOS:** Add `aw-server` to System Settings → General → Login Items.

---

## Step 2 — Run the installer

From the repo root **on your laptop**:

```bash
bash scripts/install_screen_logger.sh
```

**First run** creates `~/.config/checkin-bot/.env` and exits. Open that file and fill in:

```bash
SHEET_ID=your-google-sheet-id
GSA_KEY_PATH=/home/you/.config/checkin-bot/gsa-key.json
TIMEZONE=America/New_York   # adjust if needed
```

Drop a copy of your service-account JSON at the `GSA_KEY_PATH` location and make sure it is only readable by you:

```bash
chmod 600 ~/.config/checkin-bot/gsa-key.json
```

**Second run** installs Python dependencies, runs a smoke test, and registers the hourly job:

```bash
bash scripts/install_screen_logger.sh
```

You should see `✓ Smoke test ok.` followed by `✓ Cron installed (hourly).` (Linux) or `✓ launchd loaded.` (macOS).

---

## Step 3 — Create the sheet tab

In your Google Sheet, add a new tab named exactly **`screen_time`** and paste this as the header row (row 1):

```
local_date	laptop_screen_hours	phone_screen_hours	laptop_longest_focus_block_min	phone_longest_focus_block_min	laptop_breaks_count	phone_breaks_count
```

(Tab-separated. The columns are tab-delimited so you can paste them directly into the sheet.)

---

## Step 4 — Enable tracking in the bot

On the **server** where the bot runs, add this to your `.env`:

```bash
TRACK_SCREEN_TIME=true
```

Then restart the bot:

```bash
sudo systemctl restart checkin-bot   # or however you restart it
```

---

## Verifying it works

The screen logger writes once an hour. To check immediately:

```bash
python3 scripts/screen_logger.py
```

You should see a line like:

```
[2026-05-07] wrote: laptop_breaks_count=2, laptop_longest_focus_block_min=47, laptop_screen_hours=3.12
```

Then check your `screen_time` tab in the sheet — a row for today should have appeared.

To tail ongoing logs:

```bash
tail -f ~/.local/share/checkin-bot/screen_logger.log
```

---

## Troubleshooting

**Smoke test fails with "failed to fetch laptop bucket"**
ActivityWatch isn't running. Start it (`~/tools/activitywatch/aw-server &`) and re-run the installer.

**Smoke test passes but sheet isn't updating**
Check the cron is installed: `crontab -l`. The entry should look like:
```
0 * * * * /usr/bin/python3 /path/to/scripts/screen_logger.py >> ~/.local/share/checkin-bot/screen_logger.log 2>&1
```
Also confirm the `screen_time` tab exists in the sheet with the correct header row.

**`SHEET_ID not set` error**
Edit `~/.config/checkin-bot/.env` and add your Sheet ID. It's the long string in the sheet's URL between `/d/` and `/edit`.
