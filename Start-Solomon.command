#!/bin/bash
# Double-click this file in Finder: it opens Terminal and starts the Solomon dashboard.
cd "$(dirname "$0")" || exit 1
bash scripts/start_dashboard.sh "$@"
status=$?
echo
echo "Solomon stopped (exit $status). You can close this window."
