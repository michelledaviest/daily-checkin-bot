# Plan: Screen Time + Focus Block Tracking

## Context

Migraine correlation isn't well-served by gross daily screen-time numbers. What matters more is the **shape** of the day — long uninterrupted focus blocks accumulate eye strain in a way that two shorter blocks separated by a real break don't. The goal: capture *total hours* and *block structure* on **both laptop and phone**, separately, so analytics can spot which one drives the signal.

**Constraints:** low ongoing overhead, no paid services. ActivityWatch is FOSS (laptop *and* Android), Tailscale is free, both are one-time installs.

## Decisions locked in

- **v1A: Laptop tracking via ActivityWatch desktop.** Ship first, get value immediately.
- **v1B: Phone tracking via ActivityWatch Android.** Ship after v1A, syncs to the laptop's `aw-server` over Tailscale (Tailscale already installed).
- **v2 (deferred):** custom Android sidecar app. Probably never needed if v1B works.
- **Break threshold: 5 minutes**, both laptop and phone.
- **Six daily-row fields** (laptop and phone tracked separately):
  - `laptop_screen_hours`, `phone_screen_hours`
  - `laptop_longest_focus_block_min`, `phone_longest_focus_block_min`
  - `laptop_breaks_count`, `phone_breaks_count`
- **No manual override aliases.** Screen time is auto-only — if ActivityWatch is off, that day's row stays blank for these fields. Manual `/log` for screen time would create a temptation to fudge numbers; either the watcher recorded it or it didn't.
- **Storage:** daily row only for v1. Defer a per-block `screen_blocks` tab unless distributional analysis later justifies it.

## Architecture

ActivityWatch's design is a single local `aw-server` (HTTP API on `localhost:5600`) plus a set of "watchers" that report events into named buckets. The desktop install ships with `aw-watcher-afk` (AFK detection) and `aw-watcher-window` (active window). The Android app is its own watcher — `aw-watcher-android` — which can push events to a remote `aw-server`.

We host **one `aw-server` on the laptop** and have the phone's watcher push to it over Tailscale. The laptop ends up holding both laptop and phone events. A small Python script on the laptop runs hourly via cron / launchd, queries both buckets, computes per-device metrics, and upserts them into today's row in the Google Sheet using the **same service-account credentials** the bot uses. The bot itself doesn't change — it just reads the new columns.

Why this shape:

- **No public endpoint on the VM** — bot uses Telegram long-polling, doesn't expose HTTPS.
- **Tailscale handles reachability** — phone can sync from anywhere, not just home WiFi.
- **Same-sheet integration** — the bot's existing skip-if-already-logged logic naturally handles auto-populated columns.
- **Decoupled failure modes** — laptop down = today's row stays empty for these fields. Bot keeps working.

Trade-off: laptop has to be on / running for sync to land. Phone events queue locally when laptop is unreachable, and flush next time both are online.

---

## v1A — Laptop tracking

Ship this first. Independent of phone setup.

### Setup walkthrough (one-time, ~10 min)

```bash
# 1. Install ActivityWatch desktop (https://activitywatch.net/downloads/)
#    Run installer, launch app. It runs in the background.

# 2. Verify the API is up.
curl http://localhost:5600/api/0/info

# 3. Get a fresh service-account JSON for the laptop.
#    Console → IAM → Service Accounts → checkin-sheets-writer → Keys → Add Key → JSON
#    Save to ~/.config/checkin-bot/gsa-key.json, chmod 600.

# 4. Install Python deps and the logger.
cd ~/Documents/Personal/bots/june-checkin-bot
pip install gspread google-auth requests
bash scripts/install_screen_logger.sh
```

The installer sets up an hourly cron (Linux) or launchd (macOS) job that runs `scripts/screen_logger.py`. The first run does a smoke test — pulls today's events, computes metrics, writes to the sheet.

### What gets populated

After v1A is live, today's row picks up `laptop_screen_hours`, `laptop_longest_focus_block_min`, `laptop_breaks_count` automatically every hour. Phone columns stay blank until v1B.

---

## v1B — Phone tracking

Ship after v1A is solid (a few days of clean laptop data without the cron breaking).

### Setup walkthrough (one-time, ~15 min)

```
# 1. Install ActivityWatch on the phone.
#    F-Droid → search "ActivityWatch" → install (FOSS, recommended over Play Store version).
#    Open app, grant the "Usage access" permission when prompted.

# 2. Confirm Tailscale is running on both phone and laptop.
#    Phone: Tailscale app → connected, note the laptop's Tailscale IP (e.g. 100.64.x.x).
#    Laptop: tailscale status → confirms both devices visible.

# 3. Bind the laptop's aw-server to the Tailscale interface.
#    Edit ~/.config/activitywatch/aw-server/aw-server.toml (path may vary by OS):
#       host = "0.0.0.0"
#    Or, more secure, bind to the Tailscale IP only.
#    Restart aw-server.
#    From the phone's browser, visit http://<laptop-tailscale-ip>:5600 — you should see the AW dashboard.

# 4. Configure the phone's aw-watcher-android to push to the laptop.
#    Open the ActivityWatch Android app → Settings → "Sync to remote aw-server".
#    Enter http://<laptop-tailscale-ip>:5600.
#    Toggle "Sync now" — events should appear in the laptop's dashboard within a few minutes.

# 5. Find the phone's bucket name on the laptop.
curl http://localhost:5600/api/0/buckets | jq 'keys'
#    Look for something like "aw-watcher-android_<device-id>".
#    Note this name — the logger script needs it.

# 6. Update the logger config.
#    Edit ~/.config/checkin-bot/screen_logger.toml (or env file) to set:
#       PHONE_BUCKET=aw-watcher-android_<device-id>
#    Restart the cron job (or just wait for the next hour).
```

### What gets populated

After v1B, all six fields populate automatically every hour. The phone bucket on the laptop is updated whenever phone is reachable over Tailscale; events queue locally on phone otherwise and sync when next reachable.

---

## Schema additions

| Column | Source | Type |
|---|---|---|
| `laptop_screen_hours` | aw-watcher-afk | float |
| `phone_screen_hours` | aw-watcher-android | float |
| `laptop_longest_focus_block_min` | aw-watcher-afk | int |
| `phone_longest_focus_block_min` | aw-watcher-android | int |
| `laptop_breaks_count` | aw-watcher-afk | int |
| `phone_breaks_count` | aw-watcher-android | int |

Add to `src/sheets.py` `COLUMN_ORDER` and `docs/sheet_header.txt` in the same order. None added to `REQUIRED_FIELDS_EVENING`. No `/log` aliases — screen time is auto-only.

## File-by-file changes

### New: `scripts/screen_logger.py` (laptop-side, ~200 lines)
- Reads config: laptop bucket = `aw-watcher-afk_<hostname>` (auto-detect via `socket.gethostname()`); phone bucket = configurable env var `PHONE_BUCKET` (blank = skip phone).
- Pulls today's events from each configured bucket via `http://localhost:5600/api/0/buckets/<bucket>/events`.
- Computes per-device metrics using the block math below.
- Writes to today's row using gspread + the same service-account JSON as the bot.
- On `PHONE_BUCKET` blank, writes only laptop fields. v1A and v1B don't require code changes — just config.
- Idempotent — running multiple times in a day overwrites the columns with the latest snapshot.
- Logs to stderr; cron will email/log errors.

### New: `scripts/install_screen_logger.sh` (laptop-side)
- OS detection: macOS → launchd plist; Linux → user crontab.
- Hourly schedule.
- Prompts for service-account JSON path, copies it to `~/.config/checkin-bot/gsa-key.json` with `chmod 600`.
- Prints next-step instructions for v1B (find phone bucket, set `PHONE_BUCKET`).

### `src/sheets.py`
- Add the six new columns to `COLUMN_ORDER` (pick a sensible spot — e.g. right after the existing `desk_hours`/`couch_bed_hours` block).

### `src/telegram_handlers.py`
- No changes for v1. Screen time is auto-only.

### `src/analytics.py`
- Eventually surface block metrics in `/migraine` cross-day correlation (after ≥30 days of data):
  *"Migraine days: laptop block avg 4.2h vs your overall avg 2.8h. Phone block avg also higher: 38m vs 22m."*
- For v1, no analytics changes — just collect the data.

### `docs/sheet_header.txt`
- Append the six new columns.

### `docs/USER_GUIDE.md`
- Brief paragraph under "Daily check-ins" or a new "What gets auto-tracked" subsection: laptop + phone screen time and block structure populate automatically when ActivityWatch is running on both. Manual `/log laptop N` and `/log phone N` for overrides.

### `pyproject.toml`
- No new bot deps. The laptop logger needs `gspread`, `google-auth`, `requests` — installed manually on the laptop, not bundled with the bot.

---

## Block math

Same logic for both laptop and phone. Difference is the event semantics: laptop uses `aw-watcher-afk` events with `data.status` ∈ {"afk", "not-afk"}; phone uses `aw-watcher-android` events where presence of an event = screen on. We synthesize an "afk" / "not-afk" view for the phone by treating gaps between events as "afk."

```python
BREAK_MIN_SECONDS = 5 * 60  # 5 minutes for both devices

def compute_metrics_from_active_intervals(intervals_seconds_list):
    """intervals_seconds_list: ordered list of (start_ts, end_ts, is_active) tuples
    covering the day. is_active=True means screen on / not-afk."""
    total_active_s = sum(end - start for start, end, active in intervals_seconds_list if active)

    breaks = 0
    longest_block_s = 0
    current_block_s = 0
    for start, end, active in intervals_seconds_list:
        dur = end - start
        if active:
            current_block_s += dur
            longest_block_s = max(longest_block_s, current_block_s)
        elif dur >= BREAK_MIN_SECONDS:
            breaks += 1
            current_block_s = 0  # break long enough to reset
        # else: short gap, does not reset the block

    return {
        'screen_hours': total_active_s / 3600,
        'breaks_count': breaks,
        'longest_focus_block_min': longest_block_s // 60,
    }
```

For laptop: directly use AFK events (already have `not-afk` and `afk` semantics).
For phone: walk the android-bucket events; treat each event as an active interval; treat gaps between events as inactive intervals; feed both into the same function.

## Edge cases

- **Laptop off all day** → laptop columns stay empty. Manual `/log laptop N` to backfill if needed.
- **Phone offline / no Tailscale** → phone events queue on the device; sync on next reach. Today's row may be slightly stale until sync catches up.
- **ActivityWatch crashed silently.** No alert path in v1. If `screen_hours` has been 0 on weekdays for 3 in a row, send a Telegram nudge — add this as a follow-up if needed.
- **Phone bucket name changes** if you reinstall the Android app. Re-run step 5 of v1B setup, update `PHONE_BUCKET`.
- **Multiple laptops** (work + personal) → out of scope for v1. Hostname differs in bucket names; if needed later, sum across hostnames or pick a primary.
- **Timezone** — laptop and VM both on ET local time. ActivityWatch records UTC; logger converts to ET local date via `zoneinfo`.
- **Day rollover during a long block** — split between two days' rows. Acceptable for v1.
- **Quick phone glances inflating block count** — 5-min threshold filters most. If it ends up too noisy, raise to 10–15 min for phone only.

## Build order

1. **v1A — laptop happy path.** Write `scripts/screen_logger.py` with laptop-only support. Run manually with the laptop running ActivityWatch. Confirm sane numbers.
2. **Sheet write + schema additions.** Add the six columns. Update header. Confirm v1A populates the laptop columns end-to-end.
3. **Cron / launchd installer** for the laptop logger.
4. **v1B — phone setup.** Install ActivityWatch Android, configure Tailscale sync, find the phone bucket name, update `PHONE_BUCKET`. The same logger script picks it up — no extra code.
5. **Migraine cross-day correlation update** — once ≥30 days of data, surface block metrics. Until then, just collect.
6. **User guide doc update.**

Effort: **~half day for v1A**, another **~half day for v1B** (mostly one-time setup, not code). v2 (custom Android app) probably never happens if v1B holds up.

## Risks

- **ActivityWatch Android maturity.** Less polished than desktop watchers. Mostly works; expect some quirks. F-Droid build is the recommended one.
- **Tailscale dependency.** If Tailscale auth lapses on either device, sync stops. Tailscale is free and rarely fails, but it's a moving part.
- **`aw-server` exposed on Tailscale interface.** Anyone else on your Tailscale tailnet can hit it. Fine if it's just your devices; review if you ever add others.
- **Service-account key on laptop.** Treat like SSH key. `chmod 600`. Don't commit. Revoke and replace if laptop is lost.
- **Phone bucket name fragility.** Tied to device install ID. Reinstalling the AW Android app generates a new bucket; the old one stays in the laptop's database. Document the lookup step.
- **Block-count math edge cases.** Short AFK / brief screen-off shouldn't reset the block. Verified in the formula above. Worth a unit test.
- **Migraine correlation premature.** Don't enable the new analytics output until ≥30 days of paired data — otherwise noise will look like signal.

## Out of scope (deferred)

- **v2 custom Android app** — only if v1B fails or aw-watcher-android is too unreliable.
- **Per-block detail tab** — block-length distribution analysis. Add `screen_blocks` sheet tab if/when wanted.
- **Multi-laptop aggregation** — single laptop assumed.
- **App-level breakdown** ("how many hours in Slack vs. Chrome vs. Telegram") — the data is in the buckets but not exposed in the Sheet.
