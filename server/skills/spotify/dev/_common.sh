# Shared by pair-on-mac.sh and run.sh. Sourced, not executed.
repo="$(cd "$(dirname "$0")/../../../.." && pwd)"
persona="${HUXLEY_PERSONA:-abuelos}"
data="${SOLOIST_DATA:-$repo/server/personas/$persona/data/soloist}"
secrets="${SPOTIFY_SECRETS:-$repo/server/personas/$persona/data/secrets/spotify/values.json}"
name="${DEVICE_NAME:-Huxley}"
image=huxley-soloist-dev
container=huxley-soloist

# Always build: cached layers make it instant, and an edited entrypoint
# or Dockerfile takes effect without anyone remembering to rebuild.
docker build -q -t "$image" "$(dirname "$0")" >/dev/null
mkdir -p "$data"

# The Soloist key goes to the container through a private env file, not
# the docker command line or the terminal. (Inside the container Soloist
# takes it as an argument, so `docker top` on this machine shows it.)
envfile="$(mktemp)"
chmod 600 "$envfile"
trap 'rm -f "$envfile"' EXIT
python3 - "$secrets" "$envfile" "$name" <<'PY'
import json, sys
secrets, envfile, name = sys.argv[1:]
key = json.load(open(secrets)).get("soloist_api_key", "")
if not key:
    sys.exit(f"no soloist_api_key in {secrets}")
open(envfile, "w").write(f"SOLOIST_API_KEY={key}\nDEVICE_NAME={name}\n")
PY
