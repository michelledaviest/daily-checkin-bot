# User Guide

Everything you need to know about chatting with your check-in bot. For getting it set up in the first place, see the [README](../README.md).

## Talk to it however you want

Voice notes work. Typed messages work. The bot uses AI to pull structured data out of either, so just talk like you would to a friend.

## The daily rhythm

You'll get two nudges a day, at whatever times you set up (defaults: 9 AM and 7 PM):

- **Morning** is a light checkin. Just lifestyle reminders for the habits you are trying to build ("drink water, sit at your desk") and one ask: how many hours did you sleep?
- **Evening** is the detailed check-in. The bot asks for water, mood, body notes, shoulder pain, neck spasms, migraine status, exercise, steps, desk hours, any skipped meals, and alcohol. You can answer everything in one voice note — if you forget anything, the bot replies once with a short list of what's still missing so that you can update it.

### What a typical day sounds like

> **Bot (9 AM):** ☀️ Good morning! Reminders for today: drink water, work from your desk, no couch. Send a voice note with how many hours you slept.
>
> **You:** *"Hi! Good morning. I slept 7 and a half hours last night."*
>
> **Bot:** logged ✓ slept 7.5h
>
> **Bot (7 PM):** 🌙 Evening check-in. Send a voice note covering: water, mood, body, shoulder pain, neck spasms, migraine, exercise, steps, desk hours, any skipped meals, and alcohol.
>
> **You:** *"Drank about 50 oz, mood is a 7, shoulder pain a 3, no migraine, no neck spasms, did a 45 min spin class, around 8000 steps, 6 hours at desk, no meals skipped, no alcohol."*
>
> **Bot:** Got most of it. Still need:
> • body notes (one line on how you feel)
>
> **You:** *"Body feels fine, slightly stiff in the upper back"*
>
> **Bot:** logged ✓ 50 oz, mood 7, shoulder 3, no migraine, 45m spin, 8000 steps

## Nudges

Nudges are short, unprompted messages the bot sends you outside the structured check-in flow — celebrations, reminders, gentle warnings. There are two kinds today: **streak nudges** that appear at the end of the evening check-in, and **hydration nudges** that fire at fixed times during the day.

The four habit goals (water, sleep, steps, exercise) are configurable via env vars (`WATER_GOAL_OZ`, `SLEEP_GOAL_HOURS`, `STEPS_GOAL`, `EXERCISE_WEEKLY_GOAL_MIN`). Defaults are listed below; set your own in `.env` and restart the service to pick them up.

### Streak nudges (Habit building!)

When you finish your evening check-in, the bot tacks on a few short lines based on how you've been doing with habits that you want to build — celebrations when you're on a roll, gentle pokes when you're not.

These are the habits I have programmed in there. You can edit this list or include your own metrics/habits that you want to track.

- **💧 Hydration** — drinking enough water (default 60oz/day) counts as a streak when you string consecutive days together.
- **😴 Sleep** — getting enough hours (default 7+) a night.
- **👟 Steps** — hitting your daily target (default 10,000 steps).
- **🏃 Exercise** — your weekly minutes goal (default 150). Tracked over a rolling 7-day window so a couple of rest days won't mess things up. The bot tells you each evening how much you've moved and how many movement minutes you have left.

A streak gets announced once it's three days long. If you're under-goal two days in a row, you'll get a soft nudge ("2nd day under 60oz — bump it tomorrow?"). The bot caps things at three lines so it doesn't get overwhelming, and a special **🌟 Perfect day** line tops the message when water, sleep, and steps all hit on the same day.

A few examples of what your evening confirmation might look like:

```
logged ✓ 60 oz, mood 7, shoulder 3, no migraine, 45m spin, 11000 steps

💧 5-day streak hitting 60oz water
👟 3 days in a row 10k+ steps
🏃 180 min this week ✓ goal hit
```

```
logged ✓ 65 oz, mood 8, shoulder 2, no migraine, 30m yoga, 12000 steps

🌟 Perfect day — every habit hit
💧 7-day streak hitting 60oz water
🪑 8h at desk today, up from 6h avg 📈
👟 4-day streak 10k+ steps
```

```
logged ✓ 40 oz, mood 5, shoulder 6, no migraine, 0m exercise, 6000 steps

💧 2nd day under 60oz — bump it tomorrow?
🏃 No exercise in the last 7 days — gentle nudge
```

### Hydration nudges

A few times during the day the bot pings you to drink water and log it — a short, unscheduled message rather than a check-in. By default these fire at **11 AM, 2 PM, and 5 PM ET** (configurable via `HYDRATION_NUDGE_HOURS`).

Each nudge tells you where you are toward your daily target, with a prompt to count what you drink:

```
💧 Water break — 32oz logged so far. Drink up, then `/log water N` to count it.
```

Or if you haven't logged anything yet:

```
💧 Water break — nothing logged yet. Drink up, then `/log water N` to count it.
```

The bot stays quiet when it would be redundant:
- If you used `/log water` in the last 2 hours (so it doesn't pile up right after you've logged).
- If you've already hit your daily goal.

Tweak the schedule in `.env` (e.g. `HYDRATION_NUDGE_HOURS=10,13,16,19` for four nudges, or set it to an empty string to turn them off entirely). Restart the bot to pick up the change.

## What gets logged automatically

A few things write to the sheet in the background without you doing anything:

- **Barometric pressure** — every morning at check-in time, the bot fetches today's mean sea-level pressure and the 24-hour drop from Open-Meteo, and writes them to the `environment` tab. Useful context for migraine correlation later.
- **Laptop screen time** *(optional — off by default)* — once you set up ActivityWatch and enable it, an hourly cron job writes active screen hours, longest focus block, and break count to the `screen_time` tab. See [Setting up laptop screen time tracking](activitywatch-setup.md) to get started, then set `TRACK_SCREEN_TIME=true` in your bot's `.env` and restart.

These land in separate sheet tabs so they don't clutter the main `checkins` tab.

## Mid-day commands

### `/today` — what have I logged so far?

Useful when you're halfway through the day and can't remember whether you already told the bot about your sleep. Shows a quick rundown of what's filled in and what's still blank.

```
📅 Today (2026-05-06)
morning ✓

✓ sleep 7.5h
✓ water 32oz
✓ mood 7

Missing: body, desk, shoulder, neck spasms, migraine,
severity, exercise, exercise min, steps, skipped meals, alcohol
```

### `/log <field> <value>` — log one thing fast

When you just want to drop a number without holding a conversation. Great for incremental things like water — call it three times across the day and the totals add up.

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
/log meals yes     → skipped meals = yes.
/log alcohol 1     → +1 unit alcohol → 1 today.   (additive)
```

A few things to know:

- **Cumulative things add up:** water, steps, desk hours, exercise minutes, alcohol. So you can `/log water 32` at lunch and `/log water 16` at dinner, and the bot keeps a running total for the day.
- **Everything else replaces** the previous value (mood, sleep, pain ratings, etc.) — there's only one current answer to those.
- **`/log migraine no` resets severity to 0** so you don't have to remember a second command.
- **You can use shorthand:** `water`, `mood`, `sleep`, `desk`, `shoulder`, `neck`, `migraine`, `severity`, `exercise`, `minutes`, `steps`, `notes`/`body`, `meals`, `alcohol`. The full names work too.

### Fix something you logged earlier

You don't need a special command for this. Just send a normal message that mentions the day and what changed:

> *"Hey, update yesterday's water — I drank 64oz, not 45oz."*

The bot recognizes that as an edit (rather than a new check-in), figures out the date and the change, and replies with a confirmation card and **`Yes ✓` / `No ✗` buttons**:

> Confirm: update 2026-05-05 — water_oz 45 → 64?

Tap **Yes** to apply, or **No** to bail and try again.

A few flavors that all work:

- **Multiple things at once:** *"update yesterday's shoulder pain to 6 and water to 50"*
- **Specific dates:** *"update May 4..."*
- **Weekday phrases:** *"update last Tuesday..."* (the bot picks the most recent past Tuesday)
- If the day you mention has no row yet, the bot tells you instead of silently making one up.

Each edit also drops a little audit line into that day's transcript — `[updated 2026-05-06: water_oz 45→64]` — so you can see what changed when, if you ever look back at the sheet.

### `/migraine` — quick analytics

Pulls every row and gives you a one-shot summary: how many migraines in the last 30 and 90 days, average severity when you've been logging it, your longest streak without one, your current streak, and a day-of-the-week breakdown for spotting patterns.

```
🧠 Migraine summary

Last 30d: 4 migraines (avg severity 6.5/10)
Last 90d: 11 migraines (avg severity 6.2/10)

Longest streak without: 12 days (2026-04-12 – 2026-04-23)
Current streak without: 5 days

Day-of-week (last 90d):
Mon: 1 | Tue: 0 | Wed: 3 | Thu: 1 | Fri: 4 | Sat: 1 | Sun: 1
```

## A few smaller commands

- `/start` — quick intro and the list of available commands
- `/now morning` or `/now evening` — kick off a check-in right now (e.g. if you missed the scheduled nudge or want to log early)
- `/cancel` — bail out of an in-progress conversation
- `/status` — peek at what's in flight or whether today's archive has anything yet
