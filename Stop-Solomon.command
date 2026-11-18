#!/bin/bash
# Double-click in Finder: stops the Solomon dashboard.
cd "$(dirname "$0")" || exit 1
bash scripts/stop.sh "$@"
echo
echo "이 창은 닫으셔도 됩니다."
