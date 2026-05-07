#!/usr/bin/env bash
# Sets up scripts/screen_logger.py to run hourly on the laptop.
#
# Two-stage:
#   First run  → creates ~/.config/checkin-bot/.env with placeholders, exits.
#   Second run → installs deps, smoke-tests, installs cron (Linux) or launchd (macOS).
#
# Re-running is safe (idempotent).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGGER="$SCRIPT_DIR/screen_logger.py"
CONFIG_DIR="$HOME/.config/checkin-bot"
ENV_FILE="$CONFIG_DIR/.env"
LOG_DIR="$HOME/.local/share/checkin-bot"
LOG_FILE="$LOG_DIR/screen_logger.log"

if [ ! -f "$LOGGER" ]; then
    echo "ERROR: $LOGGER not found" >&2
    exit 1
fi

mkdir -p "$CONFIG_DIR" "$LOG_DIR"
chmod 700 "$CONFIG_DIR"

# ---- stage 1: bootstrap config -------------------------------------------

if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'EOF'
# checkin-bot screen_logger config
# Required:
SHEET_ID=

# Optional overrides:
SCREEN_TIME_TAB=screen_time
GSA_KEY_PATH=
TIMEZONE=America/New_York
AW_SERVER=http://localhost:5600
BREAK_MIN_SECONDS=300

# v1B (phone tracking) — set this once ActivityWatch Android is syncing to
# the laptop's aw-server. Find the bucket name with:
#   curl -s http://localhost:5600/api/0/buckets | python3 -c \
#     'import sys,json; [print(k) for k in json.load(sys.stdin) if "android" in k.lower()]'
PHONE_BUCKET=
EOF
    chmod 600 "$ENV_FILE"
    DEFAULT_KEY="$CONFIG_DIR/gsa-key.json"
    sed -i.bak "s|^GSA_KEY_PATH=|GSA_KEY_PATH=$DEFAULT_KEY|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
    echo
    echo "  Created $ENV_FILE"
    echo
    echo "  NEXT STEPS:"
    echo "    1. Edit $ENV_FILE and set SHEET_ID."
    echo "    2. Drop the service-account JSON to:"
    echo "         $DEFAULT_KEY"
    echo "       (chmod 600 once it's there)"
    echo "    3. Re-run this installer to finish setup."
    echo
    exit 0
fi

# ---- stage 2: install -----------------------------------------------------

# shellcheck disable=SC1090
source "$ENV_FILE"

if [ -z "${SHEET_ID:-}" ]; then
    echo "ERROR: SHEET_ID not set in $ENV_FILE" >&2
    exit 1
fi

KEY_PATH="${GSA_KEY_PATH:-$CONFIG_DIR/gsa-key.json}"
if [ ! -f "$KEY_PATH" ]; then
    echo "ERROR: service-account JSON not found at $KEY_PATH" >&2
    echo "       drop it there (chmod 600) and re-run." >&2
    exit 1
fi

# Make sure the key is not world-readable.
chmod 600 "$KEY_PATH" 2>/dev/null || true

# Verify Python deps.
if ! python3 -c "import gspread, dotenv, requests; from google.oauth2.service_account import Credentials" 2>/dev/null; then
    echo "Installing Python deps (gspread, google-auth, python-dotenv, requests)..."
    pip install --user --quiet gspread google-auth python-dotenv requests
fi

# Verify ActivityWatch is reachable.
AW_URL="${AW_SERVER:-http://localhost:5600}"
if ! curl -sf "${AW_URL}/api/0/info" >/dev/null; then
    echo "WARNING: ActivityWatch server not reachable at $AW_URL"
    echo "         (Install + run ActivityWatch first; see https://activitywatch.net)"
    echo "         Continuing anyway — cron will retry hourly."
fi

# Smoke test the logger.
echo "Running smoke test..."
if python3 "$LOGGER"; then
    echo "✓ Smoke test ok."
else
    echo "✗ Smoke test failed. Fix the above error and re-run."
    exit 1
fi

# ---- stage 3: schedule ----------------------------------------------------

PYTHON="$(command -v python3)"

case "$(uname -s)" in
    Linux)
        TMP=$(mktemp)
        crontab -l 2>/dev/null > "$TMP" || true
        # Strip any pre-existing screen_logger entry to avoid duplicates.
        grep -v "screen_logger.py" "$TMP" > "${TMP}.new" || true
        echo "0 * * * * $PYTHON $LOGGER >> $LOG_FILE 2>&1" >> "${TMP}.new"
        crontab "${TMP}.new"
        rm -f "$TMP" "${TMP}.new"
        echo "✓ Cron installed (hourly). Verify with: crontab -l"
        ;;
    Darwin)
        PLIST="$HOME/Library/LaunchAgents/com.checkin-bot.screen-logger.plist"
        cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.checkin-bot.screen-logger</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$LOGGER</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
</dict>
</plist>
EOF
        launchctl unload "$PLIST" 2>/dev/null || true
        launchctl load "$PLIST"
        echo "✓ launchd loaded. Logs: $LOG_FILE"
        ;;
    *)
        echo "ERROR: unsupported OS ($(uname -s)). Set up the cron manually." >&2
        exit 1
        ;;
esac

echo
echo "All set. Tail logs with: tail -f $LOG_FILE"
