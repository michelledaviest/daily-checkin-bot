"""Read-side queries on the Google Sheet for command summaries."""
from collections import Counter
from datetime import date, datetime, timedelta

from . import sheets
from .config import (
    EXERCISE_WEEKLY_GOAL_MIN,
    SLEEP_GOAL_HOURS,
    STEPS_GOAL,
    WATER_GOAL_OZ,
)
from .time_util import now_local

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_bool(v) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().upper() == "TRUE"


def _parse_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _parse_date(v) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(str(v).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


async def migraine_summary() -> str:
    rows = await sheets.fetch_all_rows()
    today = now_local().date()

    parsed: list[tuple[date, bool | None, int | None]] = []
    for r in rows:
        d = _parse_date(r.get("local_date"))
        if d is None:
            continue
        m = _parse_bool(r.get("migraine"))
        s = _parse_int(r.get("migraine_severity"))
        parsed.append((d, m, s))
    parsed.sort(key=lambda t: t[0])

    if not parsed:
        return "🧠 No data yet."

    def stats_for_window(days: int) -> tuple[int, float | None]:
        cutoff = today - timedelta(days=days)
        window = [(d, m, s) for d, m, s in parsed if d >= cutoff and m is not None]
        migraine_days = [t for t in window if t[1]]
        n = len(migraine_days)
        severities = [t[2] for t in migraine_days if t[2] is not None and t[2] > 0]
        avg = sum(severities) / len(severities) if severities else None
        return n, avg

    n30, avg30 = stats_for_window(30)
    n90, avg90 = stats_for_window(90)

    # Streak math — only consider rows where migraine has been recorded.
    recorded = [(d, m) for d, m, _ in parsed if m is not None]

    current_streak = 0
    for d, m in reversed(recorded):
        if m is False:
            current_streak += 1
        else:
            break

    longest = 0
    longest_start: date | None = None
    longest_end: date | None = None
    cur = 0
    cur_start: date | None = None
    for d, m in recorded:
        if m is False:
            if cur == 0:
                cur_start = d
            cur += 1
            if cur > longest:
                longest = cur
                longest_start = cur_start
                longest_end = d
        else:
            cur = 0

    # Day-of-week breakdown over last 90 days.
    weekday_counts: Counter[int] = Counter()
    cutoff_90 = today - timedelta(days=90)
    for d, m, _ in parsed:
        if d >= cutoff_90 and m is True:
            weekday_counts[d.weekday()] += 1

    lines = ["🧠 Migraine summary", ""]

    def fmt_window(days: int, n: int, avg: float | None) -> str:
        word = "migraine" if n == 1 else "migraines"
        line = f"Last {days}d: {n} {word}"
        if avg is not None:
            line += f" (avg severity {avg:.1f}/10)"
        return line

    lines.append(fmt_window(30, n30, avg30))
    lines.append(fmt_window(90, n90, avg90))
    lines.append("")

    if longest > 0 and longest_start and longest_end:
        lines.append(
            f"Longest streak without: {longest} day{'s' if longest != 1 else ''} "
            f"({longest_start.isoformat()} – {longest_end.isoformat()})"
        )
    s = "" if current_streak == 1 else "s"
    lines.append(f"Current streak without: {current_streak} day{s}")

    if any(weekday_counts.values()):
        lines.append("")
        lines.append("Day-of-week (last 90d):")
        parts = [
            f"{WEEKDAY_LABELS[i]}: {weekday_counts.get(i, 0)}" for i in range(7)
        ]
        lines.append(" | ".join(parts))

    return "\n".join(lines)


# --- Streak / habit nudges -------------------------------------------------

# Habit thresholds — all four (water/sleep/steps/exercise) are overridable in
# config.py via env vars: WATER_GOAL_OZ, SLEEP_GOAL_HOURS, STEPS_GOAL,
# EXERCISE_WEEKLY_GOAL_MIN.

# When to start announcing things.
POSITIVE_STREAK_MIN = 3   # show positive streak when this long or longer
MISS_STREAK_MIN = 2       # show miss-streak when this long or longer
STREAK_LINE_CAP = 3       # cap on streak lines (perfect-day bonus is extra)

# How many prior committed days we need before announcing the desk-hours
# baseline comparison. Below this, we don't have a meaningful avg.
DESK_BASELINE_MIN_DAYS = 5


def _is_committed(row: dict, slot_col: str) -> bool:
    """A day is committed for a slot if its *_logged_at column is non-empty."""
    return _is_filled(row.get(slot_col, ""))


def _is_filled(v) -> bool:
    return v is not None and str(v).strip() != ""


def _committed_rows_sorted(
    rows: list[dict], slot_col: str
) -> list[tuple[date, dict]]:
    """Return [(local_date, row), ...] in ascending date order, only days where
    the named slot has been committed."""
    out: list[tuple[date, dict]] = []
    for r in rows:
        d = _parse_date(r.get("local_date"))
        if d is None:
            continue
        if not _is_committed(r, slot_col):
            continue
        out.append((d, r))
    out.sort(key=lambda t: t[0])
    return out


def _daily_streak(
    committed: list[tuple[date, dict]], predicate
) -> tuple[int, int]:
    """Walk back from the most recent committed day; return (positive_streak,
    miss_streak). Both measured backward from today; only one is non-zero
    depending on what the most recent days look like."""
    pos = 0
    miss = 0
    for _, r in reversed(committed):
        met = predicate(r)
        if met is None:
            # Unknown — break the chain (don't continue counting either way).
            break
        if pos == 0 and miss == 0:
            if met:
                pos = 1
            else:
                miss = 1
        elif pos > 0:
            if met:
                pos += 1
            else:
                break
        else:  # miss > 0
            if not met:
                miss += 1
            else:
                break
    return pos, miss


def _weekly_sum(
    committed: list[tuple[date, dict]], field: str, today: date, days: int = 7
) -> float:
    """Sum a numeric field across the last `days` calendar days from today."""
    cutoff = today - timedelta(days=days - 1)
    total = 0.0
    for d, r in committed:
        if d < cutoff or d > today:
            continue
        v = _parse_float(r.get(field))
        if v is not None:
            total += v
    return total


def _baseline(
    committed: list[tuple[date, dict]], field: str, today: date, days: int = 7
) -> float | None:
    """7-day rolling average of a numeric field, EXCLUDING today.
    Returns None if fewer than DESK_BASELINE_MIN_DAYS prior committed values."""
    cutoff = today - timedelta(days=days)
    values: list[float] = []
    for d, r in committed:
        if d == today or d < cutoff or d > today:
            continue
        v = _parse_float(r.get(field))
        if v is not None:
            values.append(v)
    if len(values) < DESK_BASELINE_MIN_DAYS:
        return None
    return sum(values) / len(values)


# --- Habit predicates ------------------------------------------------------

def _water_met(r: dict) -> bool | None:
    v = _parse_float(r.get("water_oz"))
    if v is None:
        return None
    return v >= WATER_GOAL_OZ


def _sleep_met(r: dict) -> bool | None:
    v = _parse_float(r.get("sleep_hours"))
    if v is None:
        return None
    return v >= SLEEP_GOAL_HOURS


def _steps_met(r: dict) -> bool | None:
    v = _parse_int(r.get("steps"))
    if v is None:
        return None
    return v >= STEPS_GOAL


# --- Formatting ------------------------------------------------------------

def _fmt_num(v: float) -> str:
    return f"{int(v)}" if float(v).is_integer() else f"{v:g}"


def _positive_line(emoji: str, n: int, label: str) -> str:
    if n == 3:
        return f"{emoji} 3 days in a row {label}"
    return f"{emoji} {n}-day streak {label}"


def _miss_line(emoji: str, n: int, label: str) -> str:
    if n == 2:
        return f"{emoji} 2nd day {label}"
    if n == 3:
        return f"{emoji} 3rd day {label}"
    return f"{emoji} {n}th day {label}"


def _format_daily(
    emoji: str, pos: int, miss: int, pos_label: str, miss_label: str
) -> str | None:
    if pos >= POSITIVE_STREAK_MIN:
        return _positive_line(emoji, pos, pos_label)
    if miss >= MISS_STREAK_MIN:
        return _miss_line(emoji, miss, miss_label)
    return None


def _format_exercise(weekly_min: float) -> str:
    if weekly_min <= 0:
        return "🏃 No exercise in the last 7 days — gentle nudge"
    if weekly_min < EXERCISE_WEEKLY_GOAL_MIN:
        remaining = EXERCISE_WEEKLY_GOAL_MIN - weekly_min
        return f"🏃 {_fmt_num(weekly_min)} min this week — {_fmt_num(remaining)} to go"
    return f"🏃 {_fmt_num(weekly_min)} min this week ✓ goal hit"


def _format_desk(today_h: float | None, baseline_h: float | None) -> str | None:
    if today_h is None or today_h < 1:
        return None
    if baseline_h is None:
        return None
    if today_h >= baseline_h:
        if today_h > baseline_h * 1.1:
            return (
                f"🪑 {_fmt_num(today_h)}h at desk today, "
                f"up from {_fmt_num(baseline_h)}h avg 📈"
            )
        return (
            f"🪑 {_fmt_num(today_h)}h at desk today "
            f"(matching your {_fmt_num(baseline_h)}h avg)"
        )
    return None


# --- Top-level entry point -------------------------------------------------

def _build_summary(rows: list[dict], today: date) -> list[str]:
    """Pure logic — separated from the async wrapper so tests can drive it
    without monkey-patching sheets.fetch_all_rows."""
    evening_committed = _committed_rows_sorted(rows, "evening_logged_at")
    morning_committed = _committed_rows_sorted(rows, "morning_logged_at")

    if not evening_committed and not morning_committed:
        return []

    # Daily streaks. Sleep uses the morning chain since sleep is logged in the morning.
    water_pos, water_miss = _daily_streak(evening_committed, _water_met)
    steps_pos, steps_miss = _daily_streak(evening_committed, _steps_met)
    sleep_pos, sleep_miss = _daily_streak(morning_committed, _sleep_met)

    # Today's row (evening) for "perfect day" + desk hours.
    today_row: dict | None = None
    for d, r in reversed(evening_committed):
        if d == today:
            today_row = r
            break

    # Build candidate lines with sortable streak length.
    candidates: list[tuple[int, str]] = []  # (streak_length_for_sort, line)

    line = _format_daily(
        "💧", water_pos, water_miss,
        "hitting 60oz water", "under 60oz — bump it tomorrow?",
    )
    if line:
        candidates.append((max(water_pos, water_miss), line))

    line = _format_daily(
        "😴", sleep_pos, sleep_miss,
        "of 7+h sleep", "under 7h sleep — try for an early night?",
    )
    if line:
        candidates.append((max(sleep_pos, sleep_miss), line))

    line = _format_daily(
        "👟", steps_pos, steps_miss,
        "10k+ steps", "under 10k steps — a short walk tomorrow?",
    )
    if line:
        candidates.append((max(steps_pos, steps_miss), line))

    # Exercise — always show something (unless we have zero data at all).
    weekly_min = _weekly_sum(evening_committed, "exercise_minutes", today)
    candidates.append((1, _format_exercise(weekly_min)))

    # Desk hours — vs. 7-day baseline.
    today_desk = _parse_float(today_row.get("desk_hours")) if today_row else None
    desk_baseline = _baseline(evening_committed, "desk_hours", today)
    desk_line = _format_desk(today_desk, desk_baseline)
    if desk_line:
        candidates.append((1, desk_line))

    # Sort: longer streaks first; preserve insertion order on ties.
    candidates.sort(key=lambda t: -t[0])
    streak_lines = [line for _, line in candidates[:STREAK_LINE_CAP]]

    # Perfect-day bonus: all three daily habits met today.
    perfect = (
        today_row is not None
        and _water_met(today_row) is True
        and _steps_met(today_row) is True
    )
    # Sleep was logged in the morning row, may differ from today's evening row.
    today_morning_row = next(
        (r for d, r in reversed(morning_committed) if d == today), None
    )
    if perfect and today_morning_row is not None:
        perfect = _sleep_met(today_morning_row) is True
    else:
        perfect = False

    if perfect:
        return ["🌟 Perfect day — every habit hit", *streak_lines]
    return streak_lines


async def streak_summary() -> list[str]:
    """Return 0 to 4 streak/nudge lines for appending to an evening commit reply."""
    rows = await sheets.fetch_all_rows()
    return _build_summary(rows, now_local().date())
