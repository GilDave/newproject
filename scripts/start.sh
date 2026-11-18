#!/usr/bin/env bash
# Start the Solomon dashboard IN THE BACKGROUND and give the terminal back.
#
#   bash scripts/start.sh                       # start (or reopen the running one) and open the browser
#   bash scripts/start.sh --project ~/work/ena-bot
#   bash scripts/start.sh --pull                # git pull first
#   bash scripts/start.sh --no-browser          # start without opening the browser
# Stop it with:  bash scripts/stop.sh
# Other options (--config, --port, --project ...) are passed to `solomon dashboard`.
# If the dashboard is already running, --project is handed to it; other options only apply at start-up.
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

resolve_py() {  # the project's virtual environment, else a python3 that can already import solomon
  PY="${SOLOMON_PYTHON:-}"
  [ -n "$PY" ] && return 0
  if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"
  elif python3 -c 'import solomon' >/dev/null 2>&1; then PY="$(command -v python3)"
  else return 1
  fi
}

register_project() {  # register_project <dashboard url> <path>: hand a --project to the running dashboard
  "$PY" - "$1" "$2" <<'PYREG'
import json, os, sys, urllib.error, urllib.parse, urllib.request
url, path = sys.argv[1], os.path.expanduser(sys.argv[2])
u = urllib.parse.urlparse(url)
token = urllib.parse.parse_qs(u.query)["t"][0]
req = urllib.request.Request(f"http://{u.netloc}/api/project", data=json.dumps({"path": path}).encode(), method="POST",
                             headers={"X-Solomon-Token": token, "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print("  프로젝트를 추가했습니다:", json.load(r)["project"])
except urllib.error.HTTPError as e:
    print("  프로젝트를 추가하지 못했습니다:", json.load(e).get("error", e.code))
    sys.exit(1)
except Exception as e:
    print("  프로젝트를 추가하지 못했습니다:", e)
    sys.exit(1)
PYREG
}

if pid="$(running_pid)"; then
  echo "대시보드가 이미 실행 중입니다 (pid $pid)."
  if [ -f "$URLF" ]; then
    url="$(cat "$URLF")"; echo "주소: $url"
    ignored=(); i=0
    while [ "$i" -lt "${#ARGS[@]}" ]; do
      a="${ARGS[$i]}"
      case "$a" in
        --project)
          i=$((i + 1))
          if resolve_py; then register_project "$url" "${ARGS[$i]:-}"; else echo "  (Solomon 설정이 안 되어 프로젝트를 추가하지 못했습니다)"; fi ;;
        --project=*)
          if resolve_py; then register_project "$url" "${a#--project=}"; fi ;;
        *) ignored+=("$a") ;;
      esac
      i=$((i + 1))
    done
    if [ "${#ignored[@]}" -gt 0 ]; then
      echo "참고: 이미 실행 중이라 ${ignored[*]} 옵션은 적용되지 않았습니다. 적용하려면  bash scripts/stop.sh  후 다시 시작하세요."
    fi
    open_browser "$url"
  fi
  echo "멈추려면:  bash scripts/stop.sh"
  exit 0
fi
rm -f "$PIDF" "$URLF"   # leftovers of a dashboard that is gone

if [ "$PULL" = "1" ]; then
  echo "== git pull =="
  if git pull --ff-only; then :; else echo "  (pull failed: starting with the code you already have)"; fi
fi

resolve_py || { echo "Solomon is not set up yet. Run this once:  bash install.sh"; exit 1; }

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
