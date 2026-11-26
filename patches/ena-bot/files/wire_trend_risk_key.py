"""추세 봇(trend_engine)의 위험 상태를 3분봉 엔진(engine.py)과 분리한다.

문제: 두 엔진이 bot.db 의 kv 'risk_state' 하나를 같이 읽고 써서, 3분봉 엔진의 낙폭 정지가 추세 봇을 멈춘다.
변경: 추세 봇은 'trend_risk_state' 를 쓴다(없으면 새로 시작 = 이전 정지 상태를 물려받지 않음).
      대시보드 리스크 카드는 trend_risk_state 가 있으면 그것을, 없으면 예전 risk_state 를 본다.
사용(ENA 저장소 루트에서, 재실행 안전):  .venv/bin/python wire_trend_risk_key.py
"""
from pathlib import Path
import sys

MARK = "TREND_RISK_KEY"
EDITS = {
    "ena_bot/trend_engine.py": [
        ("from .store import Store\n",
         "from .store import Store\n\nTREND_RISK_KEY = \"trend_risk_state\"  # 3분봉 엔진(engine.py)의 'risk_state' 와 분리\n"),
        ('RiskState.from_dict(store.kv_get("risk_state", {}))', 'RiskState.from_dict(store.kv_get(TREND_RISK_KEY, {}))'),
        ('self._set_state("risk_state", self.risk.state.to_dict())', 'self._set_state(TREND_RISK_KEY, self.risk.state.to_dict())'),
    ],
    "tests/test_ollama_fault_injection.py": [
        ('eng.store.kv_get("risk_state")["halted"] is True', 'eng.store.kv_get("trend_risk_state")["halted"] is True'),
    ],
    "ena_bot/scores.py": [
        ('st = store.kv_get("risk_state", {}) or {}',
         'st = store.kv_get("trend_risk_state") or store.kv_get("risk_state", {}) or {}  # 추세 봇 전용 상태 우선'),
    ],
}


def main() -> int:
    for rel, edits in EDITS.items():
        p = Path(rel)
        if not p.exists():
            print(f"[오류] {rel} 없음: ENA 저장소 루트에서 실행하세요"); return 1
        s = p.read_text(encoding="utf-8")
        for old, new in edits:
            if new in s:
                continue
            if s.count(old) != 1:
                print(f"[오류] {rel}: 기준 문장을 찾지 못했거나 여러 개입니다 -> {old[:50]!r}"); return 1
            s = s.replace(old, new)
        p.write_text(s, encoding="utf-8")
        print(f"[ok] {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
