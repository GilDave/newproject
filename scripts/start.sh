#!/usr/bin/env bash
# Start the Solomon dashboard IN THE BACKGROUND and give the terminal back.
#
#   bash scripts/start.sh                       # start (or reopen the running one) and open the browser
#   bash scripts/start.sh --project ~/work/ena-bot
#   bash scripts/start.sh --pull                # git pull first
#   bash scripts/start.sh --no-browser          # start without opening the browser
# Stop it with:  bash scripts/stop.sh
# Other options (--config, --port, --project ...) are passed to `solomon dashboard`.
# State lives in ~/.solomon (override with SOLOMON_HOME): dashboard.pid, dashboard.url, dashboard.log
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
export PATH="$HOME/.local/bin:$HOME/.agy/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

STATE="${SOLOMON_HOME:-$HOME/.solomon}"
mkdir -p "$STATE" && chmod 700 "$STATE" 2>/dev/null
PIDF="$STATE/dashboard.pid"; URLF="$STATE/dashboard.url"; LOG="$STATE/dashboard.log"

OPEN=1; PULL=0; ARGS=()
for a in "$@"; do
  case "$a" in
    --no-browser) OPEN=0 ;;
    --pull) PULL=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

open_browser() {
  [ "$OPEN" = "1" ] || return 0
  if command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  fi
}

running_pid() {  # prints the pid only if it is alive AND really is our dashboard (pids get reused)
  [ -f "$PIDF" ] || return 1
  local pid; pid="$(cat "$PIDF" 2>/dev/null)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -q "solomon" || return 1
  echo "$pid"
}

if pid="$(running_pid)"; then
  echo "대시보드가 이미 실행 중입니다 (pid $pid)."
  if [ -f "$URLF" ]; then url="$(cat "$URLF")"; echo "주소: $url"; open_browser "$url"; fi
  echo "멈추려면:  bash scripts/stop.sh"
  exit 0
fi
rm -f "$PIDF" "$URLF"   # leftovers of a dashboard that is gone

if [ "$PULL" = "1" ]; then
  echo "== git pull =="
  if git pull --ff-only; then :; else echo "  (pull failed: starting with the code you already have)"; fi
fi

PY="${SOLOMON_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
  elif python3 -c 'import solomon' >/dev/null 2>&1; then PY="$(command -v python3)"
  else echo "Solomon is not set up yet. Run this once:  bash scripts/setup_mac.sh"; exit 1
  fi
fi

echo "== AI CLIs (installed? login state is not checked here) =="
for c in claude codex agy; do
  if command -v "$c" >/dev/null 2>&1; then printf '  [ok]      %s\n' "$c"; else printf '  [missing] %s\n' "$c"; fi
done

: > "$LOG"; chmod 600 "$LOG" 2>/dev/null   # the log contains the access key, keep it private
nohup "$PY" -u -m solomon dashboard --no-open ${ARGS[@]+"${ARGS[@]}"} >> "$LOG" 2>&1 &
pid=$!
echo "$pid" > "$PIDF"
disown "$pid" 2>/dev/null || true

url=""
for _ in $(seq 1 100); do   # up to ~20 s
  url="$(grep -Eo 'http://127\.0\.0\.1:[0-9]+/\?t=[A-Za-z0-9_-]+' "$LOG" 2>/dev/null | head -n 1)"
  [ -n "$url" ] && break
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "대시보드를 시작하지 못했습니다. 로그:"; tail -n 20 "$LOG"; rm -f "$PIDF"; exit 1
  fi
  sleep 0.2
done
if [ -z "$url" ]; then
  echo "대시보드가 시간 안에 시작되지 않았습니다. 로그: $LOG"; tail -n 20 "$LOG"; exit 1
fi
echo "$url" > "$URLF"; chmod 600 "$URLF" 2>/dev/null

echo
echo "대시보드를 시작했습니다 (pid $pid)."
echo "주소: $url"
echo "이 터미널은 계속 쓰셔도 됩니다. 멈추려면:  bash scripts/stop.sh"
open_browser "$url"
