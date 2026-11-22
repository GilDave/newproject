"""config.py가 hayes_holdings 키를 '알 수 없는 키'로 거부하지 않게 한다(수집기가 이 키를 직접 읽는다). 여러 번 실행해도 안전.
사용: .venv/bin/python tools/fix_config_hayes.py [프로젝트 폴더]
"""
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else ".") / "ena_bot/config.py"
s = path.read_text(encoding="utf-8")
A = "            _merge(cfg, json.load(f))\n"
B = ("            raw = json.load(f)\n"
     "            raw.pop(\"hayes_holdings\", None)  # 공개 지갑 수집기 전용 설정: ena_bot.hayes_holdings가 직접 읽는다\n"
     "            _merge(cfg, raw)\n")
if "hayes_holdings" in s:
    print("이미 적용됨")
elif s.count(A) != 1:
    raise SystemExit("고칠 위치를 찾지 못했습니다. 수정하지 않았습니다.")
else:
    path.write_text(s.replace(A, B, 1), encoding="utf-8")
    print("수정 완료: ena_bot/config.py")
