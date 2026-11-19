#!/usr/bin/env bash
# Update Solomon from its git checkout: pull the latest code and refresh the installation. (The dashboard's "업데이트" button runs this.)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
[ -d .git ] || { echo "이 폴더는 git으로 받은 것이 아니라 자동 업데이트를 할 수 없습니다."; exit 1; }
echo "== 최신 코드 받기 (git pull) =="
before="$(git rev-parse --short HEAD)"
git pull --ff-only || { echo "받지 못했습니다. 이 폴더에 직접 고친 파일이 있는지 확인하세요."; exit 1; }
after="$(git rev-parse --short HEAD)"
PY="${SOLOMON_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
echo "== 설치 갱신 =="
"$PY" -m pip install -q -e . || { echo "설치 갱신에 실패했습니다."; exit 1; }
if [ "$before" = "$after" ]; then echo "이미 최신입니다 ($after)."; else echo "업데이트했습니다: $before → $after"; fi
echo "화면의 '대시보드 재시작'을 눌러 새 버전을 적용하세요."
