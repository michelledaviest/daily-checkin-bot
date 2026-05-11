#!/usr/bin/env python3
"""One-time Garmin Connect auth — run this on your laptop.

Saves OAuth tokens to GARMIN_SESSION_DIR (default: ./garmin_tokens/ next to
this script), then prints the command to copy them to the VM.

Usage:
    python3 scripts/garmin_login.py
"""
import getpass
import os
import sys
from pathlib import Path

try:
    from garminconnect import Garmin, GarminConnectAuthenticationError
except ImportError:
    print(
        "ERROR: garminconnect not installed. Run: pip install 'garminconnect>=0.3'",
        file=sys.stderr,
    )
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SESSION_DIR = SCRIPT_DIR.parent / "garmin_tokens"
SESSION_DIR = Path(os.environ.get("GARMIN_SESSION_DIR", str(DEFAULT_SESSION_DIR)))


def _prompt_mfa() -> str:
    return input("MFA code (check your email or authenticator app): ").strip()


def main() -> None:
    print("Garmin Connect login")
    print(f"Tokens will be saved to: {SESSION_DIR}")
    print()

    email = input("Email: ").strip()
    if not email:
        print("ERROR: email is required.", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass("Password: ")

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SESSION_DIR, 0o700)

    print()
    print("Logging in — you may be prompted for an MFA code...")
    try:
        client = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
        # login(tokenstore) authenticates AND saves tokens to the directory.
        client.login(tokenstore=str(SESSION_DIR))
    except GarminConnectAuthenticationError as e:
        print(f"\nERROR: authentication failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: login failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Tokens saved to {SESSION_DIR}")
    print()
    print("Next steps:")
    print()
    print("1. Copy the tokens to the VM:")
    print(
        f"   gcloud compute scp --recurse {SESSION_DIR} checkin-bot:/opt/checkin/ --zone=<your-zone>"
    )
    print()
    print("2. On the VM, lock down the directory and update .env:")
    print("   chmod 700 /opt/checkin/garmin_tokens")
    print("   # Add to /opt/checkin/.env:")
    print("   GARMIN_ENABLED=true")
    print("   GARMIN_SESSION_DIR=/opt/checkin/garmin_tokens")
    print()
    print("3. Restart the bot:")
    print("   sudo systemctl restart checkin-bot")
    print()
    print("Token refresh: garminconnect refresh tokens last ~1 year.")
    print("If the bot alerts about auth expiry, re-run this script and SCP again.")


if __name__ == "__main__":
    main()
