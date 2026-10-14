#!/usr/bin/env bash
# Move the walkie's Telegram (calls and messages) to another phone number:
# run from the Mac, in your own terminal (Telegram sends a login code to
# that phone, and you type it here).
#
#   scripts/pi/telegram-login.sh +573001234567            # do it
#   scripts/pi/telegram-login.sh +573001234567 --dry-run  # only say what it would do
#
# On the Pi it stops the server, moves the current Telegram login (the
# session file) and the Telegram secrets into data/backup/telegram-<time>/,
# signs in with the new number (pyrogram asks for the code, and for the
# two-step password if the account has one), writes the number into
# data/secrets/telegram/values.json, and starts the server again. If the
# sign-in fails, the old login is put back and the server restarted on it.
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

# The Pi side goes over as a file and runs with this terminal attached:
# pyrogram reads the login code from the keyboard (fed on stdin, it would
# read the script instead).
remote="$(mktemp)"
trap 'rm -f "$remote"' EXIT
# shellcheck disable=SC2087  # expanded here on purpose: the phone and mode
cat > "$remote" <<REMOTE
set -euo pipefail
data="\$HOME/huxley/server/personas/abuelos/data"
py="\$HOME/huxley/.venv/bin/python"
stamp="\$(date -u +%Y%m%dT%H%M%SZ)"
backup="\$data/backup/telegram-\$stamp"
echo "Telegram moves to $phone (the current login is kept in \$backup)."
if [ "$mode" = "--dry-run" ]; then
  ls -la "\$data"/huxley_userbot.session* "\$data/secrets/telegram/values.json"
  echo "dry run: nothing changed"
  exit 0
fi
systemctl --user stop huxley
mkdir -p "\$backup"
chmod 700 "\$backup"
cp -p "\$data/secrets/telegram/values.json" "\$backup/"
for f in "\$data"/huxley_userbot.session "\$data"/huxley_userbot.session-journal; do
  [ -e "\$f" ] && mv "\$f" "\$backup/"
done
restore() {
  echo "Sign-in failed: putting the old login back."
  rm -f "\$data"/huxley_userbot.session "\$data"/huxley_userbot.session-journal
  for f in "\$backup"/huxley_userbot.session*; do [ -e "\$f" ] && cp -p "\$f" "\$data/"; done
  cp -p "\$backup/values.json" "\$data/secrets/telegram/values.json"
  systemctl --user start huxley
  exit 1
}
trap restore ERR
"\$py" - "$phone" "\$data" <<'PY'
import json, sys
from pathlib import Path
from pyrogram import Client

phone, data = sys.argv[1], Path(sys.argv[2])
secrets = data / "secrets" / "telegram" / "values.json"
values = json.loads(secrets.read_text(encoding="utf-8"))
app = Client("huxley_userbot", api_id=int(values["api_id"]), api_hash=values["api_hash"],
             workdir=str(data), phone_number=phone)
app.start()  # asks for the code Telegram sends to that phone (and a 2FA password if set)
me = app.get_me()
print(f"Signed in as {me.first_name or '?'} (number ends in {str(me.phone_number)[-4:]}).")
app.stop()
values["userbot_phone"] = phone
tmp = secrets.with_suffix(".tmp")
tmp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(secrets)
PY
trap - ERR
systemctl --user start huxley
for _ in \$(seq 1 30); do
  sleep 2
  health="\$(curl -s --max-time 3 http://127.0.0.1:8765/health || true)"
  case "\$health" in *'"telegram": "ok"'*) echo "Server back up, Telegram ok."; break ;; esac
done
case "\$health" in *'"telegram": "ok"'*) ;; *) echo "Server up, but Telegram isn't ok yet: check the log (telegram.*)." ;; esac
REMOTE
scp -q "$remote" "$pi:/tmp/huxley-telegram-login.sh"
ssh -t "$pi" "bash /tmp/huxley-telegram-login.sh; status=\$?; rm -f /tmp/huxley-telegram-login.sh; exit \$status"
[ "$mode" = "--dry-run" ] && exit 0

cat <<'NEXT'

Next:
- On the OLD account's phone: Telegram > Settings > Devices, end the walkie's session.
- Ask each family member in the contacts to send one message to the new
  number on Telegram: the walkie can only recognise a caller who has
  written to it once (Telegram's rule).
NEXT
