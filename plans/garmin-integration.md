# Plan: Garmin Connect Integration

## Context

Currently the bot asks for `steps`, `sleep_hours`, and `move_minutes` manually even though the Garmin watch already has that data. This plan auto-pulls those from Garmin into a dedicated `movement` sheet tab, with per-field fallback: if Garmin fails to populate a field, the user is asked about it in the normal check-in flow. `stress_score` is Garmin-only — no fallback, since users can't self-report HRV stress.

Garmin's official APIs require commercial registration. This plan uses the community `garth` library, which reverse-engineers the same endpoints the Garmin Connect website uses. Trade-off: Garmin can break it 1–2x/year; maintainers usually patch within days.

## Decisions locked in

- **Library:** `garminconnect>=0.3` (`python-garminconnect` by cyberjunky — actively maintained, OAuth 2.0 SSO, no browser required)
- **Sheet tab:** `movement` — all Garmin fields land here, not `checkins`
- **Fields with user fallback** (Garmin pre-fills; user is asked if Garmin fails):
  - `sleep_hours` — morning check-in
  - `steps` — evening check-in
  - `move_minutes` — evening check-in; Garmin Intensity Minutes (all movement, not just sessions)
- **Field without fallback** (Garmin-only; users can't self-report):
  - `stress_score` — Garmin's daily 0–100 HRV stress score; stays blank if Garmin fails
- **Staying in `checkins`:** `exercise_type` and `exercise_minutes` — user-logged session data ("45 min spin") that Garmin's intensity minutes don't replace
- **Not tracking:** body battery
- **Optional follow-ups:** `resting_hr`, `hrv_overnight` — easy to add to the same fetch later
- **Auth:** laptop-first interactive auth, SCP session tokens to the VM
- **Failure mode:** per-field. If Garmin fails to return a specific field, that field is absent from the pre-fill, so the check-in asks about it as normal.

## Sheet tab: `movement`

```
local_date | sleep_hours | steps | stress_score | move_minutes
```

Create this tab in the Google Sheet before deploying. Header row is in `docs/sheet_header.txt`.

## Behavior

### Fetch schedule

- **Morning slot (9 AM):** fetch last night's `sleep_hours` + 24h avg `stress_score` → write to today's `movement` row AND pre-load into the conversation's `partial_fields`.
- **Evening slot (7 PM):** fetch today's `steps` + `move_minutes` + updated `stress_score` → same.

Runs before the slot opener is sent. Fields that Garmin returns are pre-loaded so Gemini never asks about them. Fields Garmin doesn't return are absent from `partial_fields`, so Gemini asks as normal.

### Per-field fallback architecture

The scheduler calls `state.start(chat_id, slot, prefilled=garmin_data)`. `ConversationState` gets an optional `prefilled_fields` dict that is merged into `partial_fields` before the first turn. This means:

- `REQUIRED_FIELDS_MORNING` keeps `sleep_hours`
- `REQUIRED_FIELDS_EVENING` keeps `steps` and adds `move_minutes`
- When Garmin returns a field → it's in `partial_fields` → Gemini skips it
- When Garmin fails a field → it's absent → Gemini asks
- When Garmin is entirely down → all three fields are absent → Gemini asks all three, same as today

`stress_score` is never in `REQUIRED_FIELDS` — it's written silently if Garmin returns it, ignored if not.

### Commit routing

`_commit()` in `telegram_handlers.py` currently writes everything to `checkins`. After this change it splits by tab:

```python
movement_cols = set(sheets.COLUMNS["movement"])
checkins_fields = {k: v for k, v in fields.items() if k not in movement_cols}
movement_fields = {k: v for k, v in fields.items() if k in movement_cols and v is not None}

await sheets.upsert_row(today, checkins_fields)
if movement_fields:
    await sheets.upsert_row(today, movement_fields, tab="movement")
```

`sleep_hours`, `steps`, and `move_minutes` are removed from `COLUMNS["checkins"]` — they no longer appear in the `checkins` tab at all.

### `/log` overrides

`/log sleep 7.5`, `/log steps 8400`, `/log move 45` still work. These need to write to the `movement` tab instead of `checkins`. Add a `LOG_FIELD_TAB` mapping in `telegram_handlers.py` that overrides the default tab for specific fields.

### Token expiry / re-auth

On `GarminAuthError`:
1. Scheduler catches it, logs to journald.
2. Pings healthchecks heartbeat-fail URL.
3. Sends Telegram message: *"Garmin auth expired — re-run `scripts/garmin_login.py` on your laptop and SCP `garth_session/` to the VM."*
4. Bot proceeds as if Garmin returned nothing — all three fallback fields get asked in the check-in.

`garth` refresh tokens last ~1 year.

## File-by-file changes

### New: `src/garmin.py`
- `_client_lazy()` — initializes garth from `GARMIN_SESSION_DIR` on first call.
- `async fetch_morning_data() -> dict` — returns subset of `{"sleep_hours": float, "stress_score": int}`. Only includes keys that were successfully fetched.
- `async fetch_evening_data() -> dict` — returns subset of `{"steps": int, "move_minutes": int, "stress_score": int}`.
- Returns partial dicts on partial failure (e.g. stress fails but steps succeeds → `{"steps": 8200}`).
- `class GarminAuthError(Exception)` — raised only on auth failure so the alert path doesn't fire on transient errors.

### New: `scripts/garmin_login.py`
Standalone CLI for the laptop. Prompts for username, password, MFA code. Calls `garth.login()` then `garth.save(GARMIN_SESSION_DIR)`. Prints the SCP command.

### `src/state.py`
- Add optional `prefilled_fields: dict` param to `state.start()`.
- On start, merge `prefilled_fields` into `s.partial_fields` before saving.

### `src/sheets.py`
- Remove `sleep_hours`, `steps` from `COLUMNS["checkins"]` (already in `movement`).
- `move_minutes` is already only in `movement`.

### `src/prompts.py`
- `move_minutes` already added to `Fields` and field guidance.
- Add `move_minutes` to `REQUIRED_FIELDS_EVENING`.
- Remove `sleep_hours` from `REQUIRED_FIELDS_MORNING` (it stays in `Fields` for Gemini to parse if the user mentions it).
- Remove `steps` from `REQUIRED_FIELDS_EVENING`.
- Update slot opener and SYSTEM_PROMPT to not mention steps/sleep as things to cover.

### `src/telegram_handlers.py`
- `_slot_fields()`: remove `sleep_hours`, `steps` from the returned dict. Add `move_minutes`.
- `_commit()`: split fields across `checkins` and `movement` tabs (see routing logic above).
- Add `LOG_FIELD_TAB: dict[str, str]` mapping fields that write to non-default tabs:
  ```python
  LOG_FIELD_TAB = {"sleep_hours": "movement", "steps": "movement", "move_minutes": "movement"}
  ```
- Update `/log` command to use `LOG_FIELD_TAB` when calling `sheets.upsert_row`.
- Remove `sleep_hours` and `steps` from `_TODAY_DISPLAY_ORDER` in `checkins` view (or read from `movement` row — TBD).

### `src/scheduler.py`
- In `_start_slot_factory`, after the existing `_slot_already_logged` check:
  - If `GARMIN_ENABLED`: try the appropriate fetch.
  - On success: `await sheets.upsert_row(today, data, tab="movement")` + pass `prefilled=data` to `state.start()`.
  - On `GarminAuthError`: call `monitoring.garmin_auth_failed()`.
  - On any other exception: log and proceed with no prefill.

### `src/analytics.py`
- `streak_summary()` reads `sleep_hours` and `steps` from the `movement` tab instead of `checkins`.
- Merge by `local_date` before running streak logic:
  ```python
  movement_rows = await sheets.fetch_all_rows(tab="movement")
  movement_by_date = {r["local_date"]: r for r in movement_rows}
  # merge into checkins rows for streak computation
  ```

### `src/monitoring.py`
- Add `async garmin_auth_failed(bot)` — pings healthchecks fail URL and sends Telegram message.

### `src/config.py`
- Add `GARMIN_SESSION_DIR`.
- Add `GARMIN_ENABLED` (default `false`).

### `pyproject.toml`
- Add `garth>=0.4`.

### `.env.example`
- Add `GARMIN_ENABLED=false` and `GARMIN_SESSION_DIR=/opt/checkin/garth_session`.

## Auth flow

```
[laptop, one-time]
  $ pip install "garth>=0.4"
  $ python scripts/garmin_login.py
  ✓ Saved session to ./garth_session/
  Next: gcloud compute scp --recurse ./garth_session checkin-bot:/opt/checkin/ --zone=...

[VM]
  $ chmod 700 /opt/checkin/garth_session
  # Add GARMIN_ENABLED=true and GARMIN_SESSION_DIR=/opt/checkin/garth_session to .env
  $ sudo systemctl restart checkin-bot
```

Treat `garth_session/` like `.env` — gitignore, `chmod 700`, never commit.

## Build order

1. **Auth + data validation** — install garth, write `scripts/garmin_login.py`, save session, write a throwaway script to print raw responses for sleep/stress/steps endpoints. Nail down field names before writing any bot code. Sleep especially has multiple objects in the response.
2. **`src/garmin.py`** — both fetch functions, partial-dict return on partial failure, `GarminAuthError`.
3. **`src/state.py`** — add `prefilled_fields` to `state.start()`.
4. **Schema changes** — remove `sleep_hours`/`steps` from `COLUMNS["checkins"]`, create `movement` tab in the sheet, update `REQUIRED_FIELDS_EVENING` to add `move_minutes`, remove `steps`.
5. **Wire morning fetch into scheduler** — prefill + sheet write, confirm via `/today`.
6. **Wire evening fetch** — same.
7. **`_commit()` routing** — split fields across tabs.
8. **`/log` tab routing** — `LOG_FIELD_TAB` overrides.
9. **Fix analytics** — read sleep/steps from `movement`.
10. **Failure handling** — delete session on VM, confirm `GarminAuthError` fires alert and all three fallback fields get asked.
11. **Deploy + monitor for a week.**

## Risks

- **Field-name drift.** Stress score key varies by watch model. Expect archaeology in step 1.
- **Sleep date mapping.** Morning fetch asks for previous night's sleep, keyed by wake-up date (today).
- **Partial Garmin failure is now the common case.** The `fetch_*` functions must return partial dicts cleanly rather than raising on individual field failures. Test this path explicitly.
- **Garmin breaks garth.** Pin to a known-good version.
- **Rate limits.** Two fetches/day is well under any threshold.

## Out of scope

- `resting_hr`, `hrv_overnight` — add to `movement` later.
- Body battery — excluded.
- Cross-day correlation analytics (`/migraine` deepening) — separate item, builds on top of this.
