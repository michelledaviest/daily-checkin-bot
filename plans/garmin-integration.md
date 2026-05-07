# Plan: Garmin Connect Integration

## Context

Currently the bot asks the user to manually log `steps` and `sleep_hours` every check-in even though my Garmin watch already has that data. This plan auto-pulls those from Garmin and adds two new Garmin-derived fields (`stress_score`, `move_minutes`) that are hard to capture any other way.

Garmin's official APIs (Health API, Connect IQ Wellness) require commercial registration with a real product behind it — not viable for personal use. So this plan uses the **community `garth` library**, which reverse-engineers the same web/mobile endpoints the Garmin Connect website uses. Trade-off: Garmin can break the library when they update their internal API (1–2x/year), and the maintainers usually patch within days.

## Decisions locked in

- **Library:** `garth>=0.4`
- **Fields auto-populated from Garmin:**
  - `steps` (existing field, replaces manual entry)
  - `sleep_hours` (existing field, replaces manual entry)
  - `stress_score` (new — Garmin's daily 0–100 score from HRV; big migraine-correlation input)
  - `move_minutes` (new — Garmin "Intensity Minutes": moderate + 2× vigorous; distinct from `exercise_minutes` which stays as user-logged sessions)
- **Not tracking:** body battery
- **Optional follow-ups (easy to add later):** `resting_hr`, `hrv_overnight`
- **Auth strategy:** laptop-first interactive auth, then SCP session tokens to the VM
- **Failure mode:** on Garmin fetch error, fall back to asking the user the question (current behavior preserved)

## Behavior

### Fetch points

The scheduler triggers two fetches per day, piggybacking on existing slots:

- **Morning slot fire (9 AM):** before sending the opener, fetch last night's sleep + last 24h avg stress. Populate today's row.
- **Evening slot fire (7 PM):** before sending the opener, fetch today's steps + today's move_minutes + today's stress. The existing "skip if already logged" logic kicks in for any field already populated.

If a Garmin call fails (auth, network, rate limit), we log it, alert via Telegram (see *Token expiry* below), and proceed to the existing flow that asks the user.

### Skip-asking when Garmin already populated

The evening required-fields list shrinks dynamically: any field auto-populated by the morning/evening fetch is removed from the "still need" bullet list Gemini emits. Practical effect: the user sees fewer questions on days with healthy Garmin data; the conversational flow asks for steps/sleep only when Garmin couldn't deliver.

### Token expiry / re-auth

`garth` refresh tokens last ~1 year. When auth fails:
1. Bot catches the exception in `garmin.py`.
2. Logs an error with `journalctl`.
3. Pings the bot-heartbeat healthchecks URL with `/fail`.
4. Sends a Telegram message to the allowed chat: *"Garmin auth expired. Re-run `scripts/garmin_login.py` on your laptop and SCP `garth_session/` to the VM."*
5. Continues operating with manual-fallback logging until the user re-auths.

## File-by-file changes

### New: `src/garmin.py`
- `_client_lazy()` — initializes garth from `GARMIN_SESSION_DIR` on first call; reuses thereafter.
- `async fetch_morning_data() -> dict` — returns `{"sleep_hours": float, "stress_score": int}`. Pulls last night's sleep + 24h stress.
- `async fetch_evening_data() -> dict` — returns `{"steps": int, "move_minutes": int, "stress_score": int}`. Today's data.
- All fetches wrap garth calls in `asyncio.to_thread`. Errors logged + re-raised so caller can fall back.
- One `GarminAuthError` custom exception so the alert path is targeted.

### New: `scripts/garmin_login.py`
Standalone CLI for the laptop. Prompts for username, password, MFA code (sent to email by garth). Uses `garth.login()` then `garth.save(GARMIN_SESSION_DIR)`. Prints next-step SCP command.

### `src/sheets.py`
- Add columns: `stress_score`, `move_minutes` to `COLUMN_ORDER`.
- (Optional later: `resting_hr`, `hrv_overnight`.)

### `src/scheduler.py`
- In `_start_slot_factory`, before the existing `_slot_already_logged` check:
  - Try the appropriate Garmin fetch (`fetch_morning_data` for morning slot, `fetch_evening_data` for evening).
  - On success, call `sheets.upsert_row(today, fetched_dict)` to populate today's row.
  - On failure (any exception), log + alert + proceed.

### `src/telegram_handlers.py`
- In the evening check-in flow, the existing missing-fields bullet logic already drops fields with values. No code changes needed if the row is already populated by the time the user replies.
- Optional polish: the slot opener can briefly mention auto-pulled data — *"Garmin says 11,200 steps and 6h 40m sleep. What else?"* — but skip that for v1, just let the bot quietly know the answers.

### `src/monitoring.py`
- Add `garmin_auth_failed()` helper that pings the heartbeat-fail URL and sends a Telegram message via the bot.

### `src/config.py`
- Add `GARMIN_SESSION_DIR = os.environ.get("GARMIN_SESSION_DIR", "./garth_session")`.

### `pyproject.toml`
- Add `garth>=0.4`.

### `.env.example`
- `GARMIN_SESSION_DIR=/opt/checkin/garth_session`

### `docs/sheet_header.txt`
- Append new columns to the header row.

### `docs/USER_GUIDE.md`
- New section under "Nudges" or "The daily rhythm": brief note that steps/sleep/stress come from Garmin automatically; manual `/log steps` still works as override.

## Auth flow detail

```
[laptop, one-time]
  $ python scripts/garmin_login.py
  Username: thalakottur.m@northeastern.edu
  Password: ********
  MFA code (check email): 123456
  ✓ Saved session to ./garth_session/
  Next: gcloud compute scp --recurse ./garth_session checkin-bot:/opt/checkin/ --zone=us-east1-b

[laptop]
  $ gcloud compute scp --recurse ./garth_session checkin-bot:/opt/checkin/ --zone=us-east1-b

[VM]
  $ chmod 700 /opt/checkin/garth_session
  $ sudo systemctl restart checkin-bot
  $ journalctl -u checkin-bot -n 30  # watch the boot logs to confirm
```

The session directory contains pickled cookies + tokens. **Treat it like `.env` and `gsa-key.json`** — gitignore, `chmod 600`, never commit.

## Build order

1. **Local prep** — install `garth`, run `scripts/garmin_login.py` on the laptop, confirm a session is saved.
2. **`src/garmin.py` happy path** — write `fetch_morning_data` against the real account, run it locally with the saved session, confirm the values match what the Garmin Connect app shows.
3. **Schema additions** — add `stress_score`, `move_minutes` to `COLUMN_ORDER`, update sheet header.
4. **Wire into scheduler** — call from morning slot, populate row, confirm via `/today` after a manual trigger.
5. **Add evening fetch** — same pattern.
6. **Failure handling** — kill the network on the VM briefly, confirm graceful fallback + Telegram alert.
7. **Token expiry rehearsal** — manually delete the session on the VM, observe the alert path fires correctly.
8. **Deploy + monitor for a week.** Field-mapping bugs will surface in the first few days.

Realistic effort: **1–2 days of focused work** end-to-end. The trickiest parts are auth and field-mapping (figuring out which garth endpoint returns what shape; sleep especially has multiple "sleep" objects to choose from).

## Risks

- **Garmin breaks the library.** Mitigation: pin to a known-good `garth` version; check release notes before upgrading.
- **MFA stored in the session.** If your Garmin password rotates or you log in elsewhere causing forced re-auth, the session invalidates. Manual re-auth required (alert flow handles this).
- **Field-name drift.** Stress score might be `overall_stress_level` or `avg_stress_level` depending on the endpoint and watch model. First few weeks of build will involve some `print(response_json)` archaeology.
- **Sleep data is for "last night" not "today."** Map carefully — `fetch_morning_data` should ask for the previous night's sleep summary, which Garmin keys by date as the wake-up date.
- **Rate limits exist but are loose.** Two fetches/day is well under any threshold. Don't poll aggressively.

## Out of scope (deferred)

- `resting_hr`, `hrv_overnight` — easy to add to the same fetch later when wanted.
- Body battery — explicitly excluded.
- Phone screen time — separate project (Android sidecar app).
- Laptop screen time — separate (RescueTime / ActivityWatch); independent of Garmin work.
