#!/bin/bash
# Double-click in Finder: starts the Solomon dashboard in the background and opens the browser.
cd "$(dirname "$0")" || exit 1
bash scripts/start.sh "$@"
echo
echo "브라우저가 열립니다. 이 창은 닫으셔도 대시보드는 계속 실행됩니다 (멈추기: Stop-Solomon.command)."
