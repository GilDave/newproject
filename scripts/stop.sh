#!/usr/bin/env bash
# Stop the Solomon dashboard started by scripts/start.sh.
#
#   bash scripts/stop.sh                 # stop the dashboard (a run in progress keeps going)
#   bash scripts/stop.sh --with-runs     # also stop the run(s) the dashboard started (clean STOPPED)
set -u
STATE="${SOLOMON_HOME:-$HOME/.solomon}"
PIDF="$STATE/dashboard.pid"; URLF="$STATE/dashboard.url"
WITH_RUNS=0
for a in "$@"; do [ "$a" = "--with-runs" ] && WITH_RUNS=1; done

alive() { kill -0 "$1" 2>/dev/null; }
wait_gone() {  # wait_gone <pid> <seconds>
  local n=$(( $2 * 5 ))
  while [ "$n" -gt 0 ] && alive "$1"; do sleep 0.2; n=$((n - 1)); done
  ! alive "$1"
}

pid=""
if [ -f "$PIDF" ]; then
  pid="$(cat "$PIDF" 2>/dev/null)"
  if ! { [ -n "$pid" ] && alive "$pid" && ps -p "$pid" -o command= 2>/dev/null | grep -q "solomon"; }; then pid=""; fi
fi
if [ -z "$pid" ]; then
  rm -f "$PIDF" "$URLF"
  echo "대시보드가 실행 중이 아닙니다."
  exit 0
fi

# `solomon run` processes started from the dashboard are its children
runs=()
for c in $(pgrep -P "$pid" 2>/dev/null); do
  if ps -p "$c" -o command= 2>/dev/null | grep -q "solomon run"; then runs+=("$c"); fi
done
if [ "${#runs[@]}" -gt 0 ]; then
  if [ "$WITH_RUNS" = "1" ]; then
    echo "실행 중인 작업 ${#runs[@]}개를 중지합니다 (AI 호출을 끝내고 STOPPED로 기록)..."
    for c in "${runs[@]}"; do kill -TERM "$c" 2>/dev/null; done
    for c in "${runs[@]}"; do
      wait_gone "$c" 20 || { echo "  작업 $c 이(가) 응답하지 않아 강제로 종료합니다."; kill -KILL "$c" 2>/dev/null; }
    done
  else
    echo "주의: 실행 중인 작업이 ${#runs[@]}개 있습니다 (pid ${runs[*]}). 대시보드를 멈춰도 작업은 계속 진행됩니다."
    echo "      작업까지 멈추려면:  bash scripts/stop.sh --with-runs"
  fi
fi

kill -TERM "$pid" 2>/dev/null
if ! wait_gone "$pid" 10; then
  echo "대시보드가 응답하지 않아 강제로 종료합니다."
  kill -KILL "$pid" 2>/dev/null
  wait_gone "$pid" 5
fi
rm -f "$PIDF" "$URLF"
echo "대시보드를 종료했습니다."
