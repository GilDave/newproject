#!/usr/bin/env bash
# Solomon macOS setup check. Safe to re-run. Run from anywhere:  bash scripts/setup_mac.sh
# It never deletes anything: an old virtualenv with a too-old Python is renamed, not removed.
set -u
cd "$(dirname "$0")/.."

VENV_DIR="${VENV_DIR:-.venv}"
SKIP_TESTS="${SKIP_TESTS:-0}"
need_manual=()
ok()   { printf '  [ok]      %s\n' "$1"; }
warn() { printf '  [check]   %s\n' "$1"; }
bad()  { printf '  [MISSING] %s\n' "$1"; }

echo "== 1. Python 3.11+ =="
PY=""
for c in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
         /opt/homebrew/bin/python3.11 /usr/local/bin/python3.12 /usr/local/bin/python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  bad "Python 3.11 or newer was not found (macOS ships 3.9)."
  echo "      Install one:  brew install python@3.12    or download from https://www.python.org/downloads/macos/"
  echo "      Then run this script again."
  exit 1
fi
ok "$PY ($("$PY" --version 2>&1))"

echo "== 2. git / node =="
if command -v git >/dev/null 2>&1; then ok "$(git --version)"; else bad "git (run: xcode-select --install)"; need_manual+=("install git"); fi
if command -v node >/dev/null 2>&1; then ok "node $(node --version)"; else warn "node not found (only needed if you install the CLIs with npm)"; fi

echo "== 3. Virtual environment ($VENV_DIR) =="
if [ -x "$VENV_DIR/bin/python" ] && ! "$VENV_DIR/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  OLD="$VENV_DIR.old-$(date +%Y%m%d%H%M%S)"
  mv "$VENV_DIR" "$OLD"
  warn "existing $VENV_DIR used an old Python; renamed to $OLD (delete it when you like)"
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PY" -m venv "$VENV_DIR" || { bad "could not create the virtual environment"; exit 1; }
fi
ok "$VENV_DIR uses $("$VENV_DIR/bin/python" --version 2>&1)"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip || warn "pip upgrade failed (network?)"
if "$VENV_DIR/bin/python" -m pip install --quiet -e ".[dev]"; then ok "solomon installed (editable)"; else bad "pip install failed"; exit 1; fi

echo "== 4. Tests (fake CLIs, no quota used) =="
if [ "$SKIP_TESTS" = "1" ]; then warn "skipped (SKIP_TESTS=1)"; else
  if "$VENV_DIR/bin/python" -m pytest -q 2>&1 | tail -3; then :; fi
fi

echo "== 5. AI CLIs =="
"$VENV_DIR/bin/python" -m solomon doctor || need_manual+=("install the missing CLI(s) shown above and sign in")

echo "== 6. API keys in this shell (Solomon strips them, but know they exist) =="
found="$(env | grep -E '^(OPENAI_API_KEY|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|GEMINI_API_KEY|GOOGLE_API_KEY|GOOGLE_GENAI_USE_VERTEXAI|AZURE_OPENAI_API_KEY)=' | sed 's/=.*/=<hidden>/')"
if [ -n "$found" ]; then
  printf '%s\n' "$found"
  warn "the variables above are set in this shell"
else
  ok "none set"
fi

echo "== 7. Local git repository (no GitHub, no remote) =="
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ "$(git rev-parse --show-toplevel)" = "$(pwd -P)" ]; then
  ok "this folder is already a local git repository"
else
  if git init -q -b main 2>/dev/null || git init -q; then
    git add -A
    if git commit -q -m "Solomon MVP (local repository)" 2>/dev/null; then
      ok "created a local repository with the first commit (no remote is set)"
    else
      warn "repository created but the first commit failed. Set your identity once, then commit:"
      echo "      git config --global user.name \"Your Name\" && git config --global user.email you@example.com"
      echo "      git add -A && git commit -m \"Solomon MVP\""
    fi
  else
    bad "git init failed"
  fi
fi

echo
echo "== What this script cannot do for you =="
echo "  1. Sign in to each CLI with your subscription: run 'claude' and 'codex' once by hand and ask a short question."
echo "  2. Check that each subscription plan allows CLI/automation use and see its usage limits (read the terms)."
echo "  3. Make a small practice git repo with a passing test and one commit, then:"
echo "       source $VENV_DIR/bin/activate && solomon run \"<small goal>\" --project <that repo>"
for item in "${need_manual[@]:-}"; do [ -n "$item" ] && echo "  * $item"; done
exit 0
