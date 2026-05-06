"""Read-side queries on the Google Sheet for command summaries."""
from collections import Counter
from datetime import date, datetime, timedelta

from . import sheets
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
