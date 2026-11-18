#!/usr/bin/env bash
# Kept for old instructions: the installer now lives at the top of the folder.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/install.sh" "$@"
