# User Guide

How to use your check-in bot once it's deployed. For setup/deployment instructions, see the [README](../README.md).

## Replies — voice or text

Reply to the bot with **voice notes or typed text** — Gemini accepts both. Mix freely (voice the long answer, type a quick correction).

## Daily check-ins

The morning nudge fires at `MORNING_HOUR` and asks for sleep + sends lifestyle reminders. The evening nudge fires at `EVENING_HOUR` and walks you through the rest of the day's metrics. If you forget any required fields, the bot replies with a single bulleted list of what's still missing rather than asking one question at a time.

The scheduler skips the nudge if you've already logged that slot for the day (e.g. via `/log` or an early `/now evening`).

### Example conversation

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

## `/today` — show today's row

Shows what's been logged so far and what's still missing. Useful mid-day to remember whether you've already answered something.

```
📅 Today (2026-05-06)
morning ✓

✓ sleep 7.5h
✓ water 32oz
✓ mood 7

Missing: body, desk, couch/bed, shoulder, neck spasms,
migraine, severity, exercise, exercise min, steps
```

## `/log <field> <value>` — quick-log a single number

Skip the conversational flow when you want to log one thing fast.

```
/log water 32      → +32oz water → 32oz today.
/log water 16      → +16oz water → 48oz today.    (additive)
/log mood 7        → mood = 7.
/log sleep 7.5     → sleep = 7.5h.
/log shoulder 6    → shoulder = 6.
/log neck no       → neck spasms = no.
/log migraine yes  → migraine = yes.
/log severity 6    → severity = 6.
/log exercise spin → exercise = spin.
/log minutes 45    → +45 exercise min → 45 today.
/log steps 8400    → +8400 steps → 8400 today.
```

**Cumulative fields are additive** (`water_oz`, `steps`, `desk_hours`, `couch_bed_hours`, `exercise_minutes`) — call `/log water N` repeatedly through the day and it accumulates. Everything else replaces. `/log migraine no` also zeroes `migraine_severity`.

**Aliases supported:** `water`, `mood`, `sleep`, `desk`, `couch`, `shoulder`, `neck`, `migraine`, `severity`, `exercise`, `minutes`, `steps`, `notes`/`body`. Or use the canonical column names.

## Update a past day's row

Send a normal voice note or text that names the date and the change:

> *"Hi June, update yesterday's water — I drank 64oz, not 45oz."*

The bot detects this as an update (rather than a new check-in), parses the date and the field changes, then replies with a confirmation message and **inline `Yes ✓` / `No ✗` buttons**:

> Confirm: update 2026-05-05 — water_oz 45 → 64?

Tap **Yes** to apply or **No** to cancel and start over.

- **Multi-field** updates work: *"update yesterday's shoulder pain to 6 and water to 50."*
- **Absolute dates** work: *"update May 4..."*
- **Weekday phrases** work: *"update last Tuesday..."* (resolves to the most recent past Tuesday)
- If the day has no row yet, the bot says *"no row exists for X, can't update"* instead of silently creating one.
- Each applied update prepends an audit line like `[updated 2026-05-06: water_oz 45→64]` to the day's transcript column so you have a paper trail.

## `/migraine` — analytics summary

Pulls all rows and reports:

- migraine count over the **last 30 and 90 days**, with average severity (when severity > 0)
- **longest no-migraine streak** ever logged, with the date range
- **current streak** of consecutive days without a migraine
- **day-of-week breakdown** over the last 90 days — useful for spotting whether migraines cluster on specific days

```
🧠 Migraine summary

Last 30d: 4 migraines (avg severity 6.5/10)
Last 90d: 11 migraines (avg severity 6.2/10)

Longest streak without: 12 days (2026-04-12 – 2026-04-23)
Current streak without: 5 days

Day-of-week (last 90d):
Mon: 1 | Tue: 0 | Wed: 3 | Thu: 1 | Fri: 4 | Sat: 1 | Sun: 1
```

## Other commands

- `/start` — intro + command list
- `/now morning` or `/now evening` — manually start a check-in (e.g. if you missed the scheduled nudge)
- `/cancel` — abort an in-progress conversation
- `/status` — see what's mid-flight or whether today's archive has entries
