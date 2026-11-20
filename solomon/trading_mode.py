"""The paper <-> live switch for a bot, with the guards that make flipping it a deliberate act.

Going back to PAPER is always one click. Going LIVE is refused unless every automatic check passes, every manual check is ticked and the
confirmation phrase is typed. Every change writes the bot's config.json atomically, after saving a dated copy of the old one.
Nothing here restarts a service: the bot reads its config when it starts.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

CONFIRM_PHRASE = "실거래 시작"
MIN_PAPER_DAYS = 30
KEY_CHECK_MAX_AGE_H = 24
MANUAL_CHECKS = [
    ("walkforward", "표본 밖 워크포워드 검증에서 합격한 전략이라는 것을 확인했습니다"),
    ("limits", "일일 손실 한도·최대 낙폭·킬 스위치 값을 확인했습니다"),
    ("alerts", "이상이 생기면 알림(텔레그램)으로 바로 알 수 있습니다"),
    ("amount", "잃어도 되는 소액으로 시작합니다"),
]


class ModeError(Exception):
    def __init__(self, status: int, message: str, blockers: Optional[list] = None):
        super().__init__(message)
        self.status, self.message, self.blockers = status, message, blockers or []


def _cfg_path(bot_dir: str | Path) -> Path:
    return Path(bot_dir) / "config.json"


def read_config(bot_dir: str | Path) -> dict:
    try:
        return json.loads(_cfg_path(bot_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def engine_supports_live(bot_dir: str | Path) -> bool:
    """Does the strategy engine have any live broker at all? (The 4-hour trend engine only has a paper broker today.)"""
    try:
        text = (Path(bot_dir) / "ena_bot" / "trend_engine.py").read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"class\s+\w*Live\w*(Broker|Engine)|LiveBroker|LiveTrend", text))


def state(bot_dir: str | Path, health: Optional[dict], keys_check: dict, now: float, exchange: str = "bybit") -> dict:
    cfg = read_config(bot_dir)
    mode = str(cfg.get("mode", "?"))
    live_on = bool(cfg.get("live_enabled"))
    kc = (keys_check or {}).get(exchange) or {}
    key_fresh = bool(kc.get("ok")) and now - float(kc.get("checked_at") or 0) < KEY_CHECK_MAX_AGE_H * 3600
    days = float((health or {}).get("paper_days") or 0)
    checks = [
        {"id": "engine", "label": "추세 엔진에 실거래 브로커가 있다", "ok": engine_supports_live(bot_dir),
         "detail": "지금은 가상 계좌(PaperTrendBroker)만 있습니다. 실거래 브로커 개발이 먼저 필요합니다."},
        {"id": "days", "label": f"페이퍼 운영 {MIN_PAPER_DAYS}일 이상", "ok": days >= MIN_PAPER_DAYS, "detail": f"현재 {days:g}일"},
        {"id": "health", "label": "봇이 정상 동작 중이고 최근 오류가 적다", "ok": bool(health and health.get("ok") and health.get("alive") and (health.get("errors_24h") or 0) <= 3),
         "detail": "최근 24시간 오류 " + str((health or {}).get("errors_24h", "?")) + "건"},
        {"id": "keys", "label": f"{exchange} API 키가 검증됐다 (24시간 이내, 출금 권한 없음)", "ok": key_fresh and kc.get("withdraw") is False,
         "detail": "검증 안 됨" if not kc else ("출금 권한이 켜져 있습니다 — 새 키를 만드세요" if kc.get("withdraw") else "검증됨" if key_fresh else "검증이 오래됨/실패")},
    ]
    blockers = [c["label"] for c in checks if not c["ok"]]
    return {"mode": mode, "live_enabled": live_on, "is_live": live_on or mode.lower() == "live", "config_found": bool(cfg), "paper_days": days,
            "checks": checks, "manual": [{"id": i, "label": t} for i, t in MANUAL_CHECKS], "can_go_live": not blockers, "blockers": blockers,
            "phrase": CONFIRM_PHRASE}


def _write_config(bot_dir: str | Path, cfg: dict) -> None:
    p = _cfg_path(bot_dir)
    if p.exists():
        bak = p.with_name(f"config.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        bak.write_bytes(p.read_bytes())
        for old in sorted(p.parent.glob("config.json.bak-*"))[:-5]:
            old.unlink(missing_ok=True)
    tmp = p.with_name("config.json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def set_paper(bot_dir: str | Path) -> dict:
    cfg = read_config(bot_dir)
    if not cfg:
        raise ModeError(404, "봇의 config.json을 읽지 못했습니다.")
    cfg["mode"], cfg["live_enabled"] = "paper", False
    _write_config(bot_dir, cfg)
    return {"ok": True, "message": "페이퍼(가상 계좌)로 설정했습니다. 봇 서비스를 재시작하면 적용됩니다 (자동 재시작은 하지 않습니다)."}


def set_live(bot_dir: str | Path, confirm: str, manual: list, health: Optional[dict], keys_check: dict, now: float, exchange: str = "bybit") -> dict:
    st = state(bot_dir, health, keys_check, now, exchange)
    if not st["can_go_live"]:
        raise ModeError(409, "실거래로 바꿀 수 없습니다. 먼저 해결할 것이 있습니다.", st["blockers"])
    missing = [m["label"] for m in st["manual"] if m["id"] not in set(manual or [])]
    if missing:
        raise ModeError(400, "직접 확인해야 하는 항목을 모두 체크하세요.", missing)
    if (confirm or "").strip() != CONFIRM_PHRASE:
        raise ModeError(400, f"확인 문구 '{CONFIRM_PHRASE}'를 정확히 입력하세요.")
    cfg = read_config(bot_dir)
    cfg["mode"], cfg["live_enabled"] = "live", True
    _write_config(bot_dir, cfg)
    return {"ok": True, "message": "config.json을 실거래로 바꿨습니다(이전 파일은 백업). 봇 서비스를 재시작해야 적용되며, 자동 재시작은 하지 않습니다."}
