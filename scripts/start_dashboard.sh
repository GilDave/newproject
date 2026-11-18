#!/usr/bin/env bash
# Start the Solomon dashboard with one command (macOS).
#
#   bash scripts/start_dashboard.sh                      # remembered projects, browser opens
#   bash scripts/start_dashboard.sh --project ~/work/ena-bot
#   bash scripts/start_dashboard.sh --pull               # git pull first, then start
#   bash scripts/start_dashboard.sh --config ~/my.toml --port 9000 --no-open
#
# Everything except --pull is passed to `solomon dashboard`. Closing this terminal window stops the dashboard.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# A double-clicked launcher starts with a minimal PATH: add the places the AI CLIs are installed in.
export PATH="$HOME/.local/bin:$HOME/.agy/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PULL=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--pull" ]; then PULL=1; else ARGS+=("$a"); fi
done

if [ "$PULL" = "1" ]; then
  echo "== git pull =="
  if git pull --ff-only; then :; else echo "  (pull failed: starting with the code you already have)"; fi
fi

# Python: the project's virtual environment, else a python3 that can already import solomon.
PY="${SOLOMON_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
  elif python3 -c 'import solomon' >/dev/null 2>&1; then PY="$(command -v python3)"
  else
    echo "Solomon is not set up yet. Run this once:  bash scripts/setup_mac.sh"
    exit 1
  fi
fi

echo "== AI CLIs (installed? login state is not checked here) =="
for c in claude codex agy; do
  if command -v "$c" >/dev/null 2>&1; then printf '  [ok]      %s\n' "$c"; else printf '  [missing] %s\n' "$c"; fi
done
echo

exec "$PY" -m solomon dashboard "${ARGS[@]+"${ARGS[@]}"}"
