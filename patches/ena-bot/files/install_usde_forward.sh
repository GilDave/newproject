#!/bin/bash
# ENA 전진검증(USDe 신호) 자동화 설치 — 맥, sudo 불필요. ENA 폴더에서 실행한다.
#   bash install_usde_forward.sh <커밋해시>        스크립트 받기 + launchd 등록(매일 09:23) + 지금 1회 실행
#   bash install_usde_forward.sh --uninstall       등록 해제(기록 파일은 남긴다)
#   bash install_usde_forward.sh --render-only <폴더>   시험용: plist 만 만든다
# 하는 일은 읽기 전용이다: 공개 API 로 USDe 유통량을 받아 data/forward/usde_log.csv 에 기록하고, ENA 시세를 이어받아 평가를 data/forward/usde_eval.txt 에 쓴다.
# 매매·설정·bot.db 는 건드리지 않는다.
set -euo pipefail

LABEL="com.ena.usdeforward"
WORKDIR="$(pwd)"
FILES="ena_macro_study.py ena_idea_lab.py ena_altdata_lab.py ena_usde_forward.py"
AGENT_DIR="$HOME/Library/LaunchAgents"

render() {  # $1 = plist 를 만들 폴더. 경로 이스케이프는 plistlib 에 맡긴다.
  mkdir -p "$1"
  local py="$WORKDIR/.venv/bin/python"
  [ -x "$py" ] || py="$(command -v python3)"
  WORKDIR="$WORKDIR" LABEL="$LABEL" "$py" - "$1" <<'PY'
import os, plistlib, sys
w, label = os.environ["WORKDIR"], os.environ["LABEL"]
d = {
    "Label": label,
    "ProgramArguments": [w + "/.venv/bin/python", "-W", "ignore", "tools/research/ena_usde_forward.py", "--daily"],
    "WorkingDirectory": w,
    "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    "RunAtLoad": False,
    "StartCalendarInterval": {"Hour": 9, "Minute": 23},   # 맥 현지 시각. DefiLlama 일 단위 값은 00:00 UTC 에 갱신된다(한국 09:00)
    "StandardOutPath": w + "/data/" + label + ".out.log",
    "StandardErrorPath": w + "/data/" + label + ".err.log",
}
with open(os.path.join(sys.argv[1], label + ".plist"), "wb") as fh:
    plistlib.dump(d, fh)
PY
}

domain() { if launchctl print "gui/$(id -u)" >/dev/null 2>&1; then echo "gui/$(id -u)"; else echo "user/$(id -u)"; fi; }

case "${1:-}" in
  --render-only)
    render "${2:?폴더를 지정하세요}"; exit 0 ;;
  --uninstall)
    D="$(domain)"
    launchctl bootout "$D/$LABEL" 2>/dev/null || true
    rm -f "$AGENT_DIR/$LABEL.plist"
    echo "등록 해제했습니다. 기록(data/forward/usde_log.csv)은 그대로 남아 있습니다."; exit 0 ;;
  ""|-h|--help)
    sed -n 2,8p "$0"; exit 1 ;;
esac

REV="$1"
[ -d "$WORKDIR/ena_bot" ] && [ -x "$WORKDIR/.venv/bin/python" ] || { echo "ENA 폴더(ena_bot 와 .venv 가 있는 곳)에서 실행하세요."; exit 1; }
BASE="https://raw.githubusercontent.com/GilDave/newproject/$REV/patches/ena-bot/files"

mkdir -p tools/research data/forward
for f in $FILES; do
  curl -fsSL "$BASE/$f" -o "tools/research/$f"
  "$WORKDIR/.venv/bin/python" -m py_compile "tools/research/$f"
done
echo "스크립트 4개를 tools/research/ 에 받았습니다."

mkdir -p "$AGENT_DIR"
render "$AGENT_DIR"
plutil -lint "$AGENT_DIR/$LABEL.plist" >/dev/null
D="$(domain)"
launchctl enable "$D/$LABEL" 2>/dev/null || true
launchctl bootout "$D/$LABEL" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do launchctl print "$D/$LABEL" >/dev/null 2>&1 || break; sleep 0.5; done
launchctl bootstrap "$D" "$AGENT_DIR/$LABEL.plist"
echo "등록했습니다: $LABEL (매일 09:23). 슬립 중이면 깨어난 뒤 한 번 실행됩니다."

echo "== 지금 1회 실행 (첫 기록이 전진검증의 시작점이 됩니다) =="
"$WORKDIR/.venv/bin/python" -W ignore tools/research/ena_usde_forward.py --daily || echo "(첫 실행이 실패했습니다. 위 메시지를 확인하세요. 등록 자체는 끝났습니다.)"
echo
echo "확인:  cat data/forward/usde_eval.txt     기록: tail -3 data/forward/usde_log.csv     해제: bash $0 --uninstall"
