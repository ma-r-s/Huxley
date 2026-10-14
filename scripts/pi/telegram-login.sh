#!/usr/bin/env bash
# Move the walkie's Telegram (calls and messages) to another phone number:
# run from the Mac, in your own terminal (Telegram sends a login code to
# that phone, and you type it here).
#
#   scripts/pi/telegram-login.sh +573001234567            # do it
#   scripts/pi/telegram-login.sh +573001234567 --dry-run  # only say what it would do
#
# 1. Signs in with the new number under a temporary login file on the Pi
#    while the server keeps running (pyrogram asks for the code, and for
#    the two-step password if the account has one, hidden). Ctrl-C or a
#    dropped connection here changes nothing: the temporary file is removed.
# 2. Only then: stops the server for a few seconds, keeps the current login
#    and the Telegram secrets in data/backup/telegram-<time>/, puts the new
#    login in place, writes the number into data/secrets/telegram/values.json
#    and starts the server again (anything failing here restores the backup).
# Nothing is printed from the secrets.
set -euo pipefail

pi="mario@100.75.152.70"
phone="${1:-}"
mode="${2:-}"
if ! [[ "$phone" =~ ^\+[0-9]{7,15}$ ]]; then
  echo "usage: $0 +<country code><number> [--dry-run]   (e.g. +573001234567)" >&2
  exit 2
fi
if [ -n "$mode" ] && [ "$mode" != "--dry-run" ]; then
  echo "unknown option: $mode" >&2
  exit 2
fi

here="$(mktemp -d)"
trap 'rm -rf "$here"' EXIT

# The sign-in, run as a file so pyrogram reads the code from the keyboard.
cat > "$here/login.py" <<'PY'
import json, sys
from pathlib import Path
from pyrogram import Client

phone, data = sys.argv[1], Path(sys.argv[2])
values = json.loads((data / "secrets" / "telegram" / "values.json").read_text(encoding="utf-8"))
app = Client("huxley_userbot_new", api_id=int(values["api_id"]), api_hash=values["api_hash"],
             workdir=str(data), phone_number=phone, hide_password=True)
app.start()  # asks for the code Telegram sends to that phone (and a 2FA password if set)
me = app.get_me()
print(f"Signed in as {me.first_name or '?'} (number ends in {str(me.phone_number)[-4:]}).")
app.stop()
PY

# The swap, after a good sign-in: writes the number into the secrets.
cat > "$here/number.py" <<'PY'
import json, sys
from pathlib import Path

phone, secrets = sys.argv[1], Path(sys.argv[2])
values = json.loads(secrets.read_text(encoding="utf-8"))
values["userbot_phone"] = phone
tmp = secrets.with_suffix(".tmp")
tmp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(secrets)
PY

cat > "$here/remote.sh" <<'SH'
set -euo pipefail
phone="$1"; mode="$2"; tools="$3"
data="$HOME/huxley/server/personas/abuelos/data"
py="$HOME/huxley/.venv/bin/python"
backup="$data/backup/telegram-$(date -u +%Y%m%dT%H%M%SZ)"
echo "Telegram moves to $phone (the current login will be kept in $backup)."
if [ "$mode" = "--dry-run" ]; then
  ls -la "$data"/huxley_userbot.session* "$data/secrets/telegram/values.json"
  systemctl --user is-active huxley
  echo "dry run: nothing changed"
  exit 0
fi

new="$data/huxley_userbot_new"
cleanup_new() { rm -f "$new".session "$new".session-journal; }
trap cleanup_new EXIT INT TERM HUP
cleanup_new
"$py" "$tools/login.py" "$phone" "$data"   # the server keeps running meanwhile

# Signed in: swap, with the server stopped for a few seconds.
trap '' INT TERM HUP   # the swap is short; don't leave it half done
restore() {
  echo "Something failed while swapping: putting the old login back."
  rm -f "$data"/huxley_userbot.session "$data"/huxley_userbot.session-journal
  for f in "$backup"/huxley_userbot.session*; do [ -e "$f" ] && cp -p "$f" "$data/"; done
  [ -e "$backup/values.json" ] && cp -p "$backup/values.json" "$data/secrets/telegram/values.json"
  systemctl --user start huxley
  exit 1
}
trap restore ERR
systemctl --user stop huxley
mkdir -p "$backup"
chmod 700 "$backup"
cp -p "$data/secrets/telegram/values.json" "$backup/"
for f in "$data"/huxley_userbot.session "$data"/huxley_userbot.session-journal; do
  if [ -e "$f" ]; then mv "$f" "$backup/"; fi
done
mv "$new.session" "$data/huxley_userbot.session"
"$py" "$tools/number.py" "$phone" "$data/secrets/telegram/values.json"
trap - ERR
systemctl --user start huxley
health=""
for _ in $(seq 1 30); do
  sleep 2
  health="$(curl -s --max-time 3 http://127.0.0.1:8765/health || true)"
  case "$health" in *'"telegram": "ok"'*) echo "Server back up, Telegram ok."; break ;; esac
done
case "$health" in
  *'"telegram": "ok"'*) ;;
  *) echo "Server up, but Telegram isn't ok yet: check the log (telegram.*)." ;;
esac
SH

remote_dir="/tmp/huxley-telegram-login"
ssh "$pi" "rm -rf $remote_dir && mkdir -m 700 $remote_dir"
scp -q "$here/login.py" "$here/number.py" "$here/remote.sh" "$pi:$remote_dir/"
status=0
ssh -t "$pi" "bash $remote_dir/remote.sh '$phone' '$mode' $remote_dir" || status=$?
ssh "$pi" "rm -rf $remote_dir"
[ "$status" -ne 0 ] && exit "$status"
[ "$mode" = "--dry-run" ] && exit 0

cat <<'NEXT'

Next:
- On the OLD account's phone: Telegram > Settings > Devices, end the walkie's session.
- Ask each family member in the contacts to send one message to the new
  number on Telegram: the walkie can only recognise a caller who has
  written to it once (Telegram's rule).
NEXT
