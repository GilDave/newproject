#!/usr/bin/env bash
# Restart the dashboard (the dashboard's "재시작" button runs this, detached). Keeps the same address.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
sleep 1
bash scripts/stop.sh >/dev/null 2>&1
sleep 1
bash scripts/start.sh --no-browser >/dev/null 2>&1
