"""경제지표 발표 일정 수집(읽기 전용): 미국 지표의 발표 시각·이전·예상·발표값을 외부 공개 피드에서 받아 캐시하고, 대시보드 경제 캘린더에 합친다.

- 기존 events.py 는 FOMC·CPI·고용보고서 세 종류만 하드코딩돼 있어 도매재고·소매재고·PCE·실업수당 같은 지표는 담지 못한다. 이 모듈이 그 빈자리를 채운다.
- 출처: 공개 주간 일정 JSON(nfs.faireconomy.media). 키가 필요 없다. 브라우저인 척하는 식별자를 붙이면 막히는 사이트가 있어 curl 기본 식별자로 받는다.
- 피드에 없는 지표는 data/econ_manual.json 에 직접 적을 수 있다(python -m ena_bot.econ_calendar add ...).
- 발표 시각 가까이에는 더 자주(10분) 받아 '발표값'을 채우되 피드 제공자에게 부담을 주지 않도록 평소에는 1시간에 한 번만 받는다.
- 네트워크가 막히거나 실패하면 마지막 캐시를 그대로 보여 주고 실패 사실을 화면에 알린다. 매매·설정은 건드리지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
KST = "Asia/Seoul"
FEED_URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
COUNTRIES = ("USD",)
IMPACT_RANK = {"high": 3, "medium": 2, "low": 1, "holiday": 0}
TTL_NORMAL, TTL_NEAR, TTL_RETRY = 3600, 600, 600          # 평소 / 발표 직전·직후 / 실패 뒤 재시도 (초)
NEAR_BEFORE, NEAR_AFTER = 20 * 60, 90 * 60                # 발표 20분 전부터 90분 뒤까지 '가까움'
STATIC_KEYS = ("FOMC", "CPI", "NFP")                       # events.py 가 이미 달력에 그리는 종류(중복 표시 방지)

# 영문 제목(소문자) 패턴 → 한국어. 위에서부터 처음 맞는 것을 쓴다. 맞는 것이 없으면 영문 제목을 그대로 보여 준다.
KO = [
    (r"core pce", "근원 PCE 물가"), (r"pce price", "PCE 물가"), (r"personal spending", "개인소비지출"), (r"personal income", "개인소득"),
    (r"core cpi", "근원 소비자물가(CPI)"), (r"\bcpi\b", "소비자물가(CPI)"), (r"core ppi", "근원 생산자물가(PPI)"), (r"\bppi\b", "생산자물가(PPI)"),
    (r"non-?farm", "비농업 고용(NFP)"), (r"unemployment rate", "실업률"), (r"average hourly earnings", "평균 시간당 임금"),
    (r"unemployment claims|jobless claims", "신규 실업수당 청구건수"), (r"adp", "ADP 민간 고용"), (r"jolts", "JOLTS 구인건수"),
    (r"federal funds rate|fomc statement", "FOMC 금리 결정"), (r"fomc.*minutes|meeting minutes", "FOMC 의사록"), (r"fomc press|press conference", "FOMC 기자회견"),
    (r"powell|fed chair", "연준 의장 발언"),
    (r"wholesale inventor", "도매재고 전월비"), (r"retail inventor", "소매재고 전월비"), (r"business inventor", "기업재고 전월비"),
    (r"core retail sales", "근원 소매판매"), (r"retail sales", "소매판매"), (r"durable goods", "내구재 주문"), (r"factory orders", "공장 주문"),
    (r"ism manufacturing|manufacturing pmi", "ISM 제조업 PMI"), (r"ism services|services pmi", "ISM 서비스업 PMI"), (r"flash .*pmi|s&p global", "S&P 글로벌 PMI"),
    (r"gdp", "GDP"), (r"trade balance", "무역수지"), (r"goods trade", "상품 무역수지"),
    (r"consumer confidence", "소비자신뢰지수"), (r"consumer sentiment|michigan", "미시간대 소비자심리"), (r"inflation expectations", "기대 인플레이션"),
    (r"housing starts", "주택착공"), (r"building permits", "건축허가"), (r"existing home sales", "기존주택 판매"), (r"new home sales", "신규주택 판매"),
    (r"pending home sales", "잠정주택 판매"), (r"industrial production", "산업생산"), (r"capacity utilization", "설비가동률"),
    (r"crude oil inventories", "원유 재고"), (r"natural gas storage", "천연가스 재고"), (r"empire state", "뉴욕 연은 제조업지수"), (r"philly fed", "필라델피아 연은 제조업지수"),
    (r"treasury|bond auction|note auction|bill auction", "미 국채 입찰"), (r"bank holiday", "미국 휴장"),
]


LEVEL_NAMES = ("소매판매", "근원 소매판매", "내구재 주문", "공장 주문", "산업생산", "개인소비지출", "개인소득")


def ko_name(title: str) -> str:
    low = title.lower()
    for pat, ko in KO:
        if re.search(pat, low):
            if ko in LEVEL_NAMES and "m/m" in low:
                ko += " 전월비"
            if ko == "소매재고 전월비" and "ex auto" in low:
                ko += " (자동차 제외)"
            return ko
    return title


def category(title: str) -> str | None:
    low = title.lower()
    if re.search(r"federal funds rate|fomc statement|fomc press", low):
        return "FOMC"
    if re.search(r"\bcpi\b", low):
        return "CPI"
    if re.search(r"non-?farm", low):
        return "NFP"
    return None


def _to_ms(s: str) -> int | None:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=ZoneInfo("America/New_York"))
        return int(d.timestamp() * 1000)
    except (ValueError, TypeError, AttributeError):
        return None


def parse_feed(text: str, countries=COUNTRIES) -> list[dict]:
    """주간 일정 JSON → 정규화한 목록. 형식이 달라도 읽을 수 있는 항목만 건지고, 아무것도 못 읽으면 ValueError."""
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("일정 피드의 최상위가 목록이 아닙니다")
    out = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        ts, title = _to_ms(str(x.get("date", ""))), str(x.get("title") or x.get("event") or "").strip()
        if ts is None or not title or (countries and str(x.get("country", "")).upper() not in countries):
            continue
        imp = str(x.get("impact", "")).strip().lower() or "low"
        out.append({"ts": ts, "name_en": title, "name": ko_name(title), "country": str(x.get("country", "")).upper(), "impact": imp,
                    "forecast": str(x.get("forecast") or "").strip(), "previous": str(x.get("previous") or "").strip(),
                    "actual": str(x.get("actual") or "").strip(), "category": category(title), "manual": False})
    if raw and not out:
        raise ValueError("읽을 수 있는 항목이 없습니다(형식 변경 또는 해당 국가 일정 없음)")
    return out


def http_get(url: str, timeout: int = 20) -> str:
    """curl 로 받는다(식별자 미지정). 실패하면 OSError."""
    try:
        r = subprocess.run(["curl", "--http1.1", "-fsSL", "-m", str(timeout), url], capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        raise OSError(f"{timeout}초 안에 받지 못했습니다") from None
    if r.returncode != 0:
        raise OSError(f"curl 종료 코드 {r.returncode}: {(r.stderr or '').strip()[:80]}")
    return r.stdout


# ------------------------------------------------------------------ 수동 입력
def parse_manual(rows, tz: str = KST) -> list[dict]:
    out = []
    zone = ZoneInfo(tz)
    for x in rows if isinstance(rows, list) else []:
        try:
            when = datetime.strptime(str(x["when"]), "%Y-%m-%d %H:%M").replace(tzinfo=zone)
            name = str(x["name"]).strip()
        except (KeyError, ValueError):
            continue
        out.append({"ts": int(when.timestamp() * 1000), "name_en": name, "name": name, "country": "USD", "impact": str(x.get("impact", "low")).lower(),
                    "forecast": str(x.get("forecast", "")), "previous": str(x.get("previous", "")), "actual": str(x.get("actual", "")),
                    "category": category(name), "manual": True})
    return out


def merge(feed: list[dict], manual: list[dict]) -> list[dict]:
    """같은 시각·같은 이름이면 수동 입력의 값(특히 발표값)이 빈 칸을 채운다. 피드에 없는 수동 항목은 그대로 더한다."""
    out = {(e["ts"], e["name"]): dict(e) for e in feed}
    for m in manual:
        k = (m["ts"], m["name"])
        if k in out:
            for f in ("forecast", "previous", "actual"):
                if m[f] and not out[k][f]:
                    out[k][f] = m[f]
            out[k]["manual"] = True
        else:
            out[k] = dict(m)
    return sorted(out.values(), key=lambda e: (e["ts"], e["name"]))


# ------------------------------------------------------------------ 캐시·수집기
class Collector:
    def __init__(self, data_dir: Path | str = "data", fetch=http_get, urls=FEED_URLS, clock=time.time, network: bool | None = None):
        self.dir, self.fetch, self.urls, self.clock = Path(data_dir), fetch, urls, clock
        self.network = (not os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("ENA_ECON_NETWORK") == "1") if network is None else network
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_try = 0.0
        self.error = ""

    @property
    def cache_path(self) -> Path:
        return self.dir / "econ_calendar.json"

    @property
    def manual_path(self) -> Path:
        return self.dir / "econ_manual.json"

    def load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def manual(self) -> list[dict]:
        try:
            return parse_manual(json.loads(self.manual_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return []

    def events(self) -> list[dict]:
        return merge(self.load_cache().get("events", []), self.manual())

    def due(self, now_ms: int) -> bool:
        cache = self.load_cache()
        fetched = cache.get("fetched_at", 0)
        near = any(e["ts"] - NEAR_BEFORE * 1000 <= now_ms <= e["ts"] + NEAR_AFTER * 1000 for e in cache.get("events", []))
        ttl = TTL_NEAR if near else TTL_NORMAL
        since_try = (now_ms - self._last_try * 1000) / 1000
        if not fetched:
            return since_try >= TTL_RETRY or self._last_try == 0
        if self.error and since_try < TTL_RETRY:       # 실패한 뒤에는 잠시 쉰다
            return False
        return (now_ms - fetched) / 1000 >= ttl

    def refresh(self) -> dict:
        """피드를 받아 캐시를 갱신한다. 실패하면 캐시는 그대로 두고 오류만 기록한다."""
        now_ms = int(self.clock() * 1000)
        self._last_try = self.clock()
        events, errs = [], []
        for url in self.urls:
            try:
                events += parse_feed(self.fetch(url))
            except Exception as exc:  # noqa: BLE001
                errs.append(f"{url.rsplit('/', 1)[-1]}: {str(exc)[:70]}")
        if events:
            uniq = {(e["ts"], e["name_en"]): e for e in events}
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"fetched_at": now_ms, "source": "nfs.faireconomy.media", "events": sorted(uniq.values(), key=lambda e: e["ts"]),
                                       "errors": errs}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)
            self.error = ""
            return {"ok": True, "count": len(uniq), "errors": errs}
        self.error = "; ".join(errs) or "받은 일정이 없습니다"
        return {"ok": False, "count": 0, "errors": errs}

    def poke(self) -> None:
        """필요하면 백그라운드에서 한 번 갱신을 시작한다(요청을 막지 않는다)."""
        if not self.network:
            return
        with self._lock:
            if (self._thread is not None and self._thread.is_alive()) or not self.due(int(self.clock() * 1000)):
                return
            self._thread = threading.Thread(target=self.refresh, daemon=True)
            self._last_try = self.clock()
            self._thread.start()


COLLECTOR = Collector()


def configure(data_dir) -> None:
    global COLLECTOR
    COLLECTOR = Collector(data_dir)


def day_str(ms: int, tz: str = KST) -> str:
    return datetime.fromtimestamp(ms / 1000, ZoneInfo(tz)).strftime("%Y-%m-%d")


def fetch_calendar_plus(now_ms: int, base: dict, collector: Collector | None = None, tz: str = KST) -> dict:
    """기존 events.py 달력(base)에 외부 지표 일정을 합친다. 월간 격자에는 중요도 높음만(기존 FOMC·CPI·NFP 와 겹치는 것은 제외),
    '오늘의 지표'와 '앞으로 일정'에는 전부 넣는다. base 의 구조(events/items/coverage 등)는 그대로 유지한다."""
    c = collector or COLLECTOR
    c.poke()
    cache = c.load_cache()
    evs = c.events()
    today = day_str(now_ms, tz)
    out = dict(base)
    static_days = {(e["date"], e["name"]) for e in base.get("events", [])}          # events.py 가 이미 그린 (날짜, FOMC|CPI|NFP)
    grid = [e for e in evs if IMPACT_RANK.get(e["impact"], 1) >= 3 and (day_str(e["ts"], tz), e["category"]) not in static_days]
    out["events"] = list(base.get("events", [])) + [{"name": e["name"], "ts": e["ts"], "date": day_str(e["ts"], tz), "source": "지표 피드"} for e in grid]
    out["today"] = [e for e in evs if day_str(e["ts"], tz) == today]
    out["upcoming_econ"] = [e for e in evs if e["ts"] > now_ms and day_str(e["ts"], tz) != today][:8]
    fetched = cache.get("fetched_at")
    out["econ"] = {"fetched_at": fetched, "count": len(cache.get("events", [])), "manual": len(c.manual()), "error": c.error,
                   "source": cache.get("source", "nfs.faireconomy.media"), "network": c.network}
    if fetched:
        out["updated_at"] = int(fetched)
        out["source"] = base.get("source", "") + " + 외부 지표 피드"
    nxt = [e for e in evs if e["ts"] > now_ms][:5]
    if nxt:                                                   # 앞으로 5건은 정적 일정과 외부 일정을 합쳐 가까운 순으로
        static = [i for i in base.get("items", [])]
        merged = sorted(static + [{"name": e["name"], "ts": e["ts"], "hours": round((e["ts"] - now_ms) / 3_600_000, 1)} for e in nxt], key=lambda i: i["ts"])
        seen, items = set(), []
        for i in merged:
            if (i["ts"], i["name"]) not in seen:
                seen.add((i["ts"], i["name"]))
                items.append(i)
        out["items"] = items[:5]
    # events.py 의 FOMC·CPI·NFP 일정이 소진돼도 외부 피드에 앞으로 일정이 있으면 '갱신 필요' 경고를 거두고 정상으로 본다
    covered = {e["category"] for e in evs if e["ts"] > now_ms and e["category"]}
    stale = [k for k in base.get("stale_kinds", []) if k not in covered]
    if base.get("stale_kinds") and len(stale) < len(base["stale_kinds"]):
        out["stale_kinds"] = stale
        out["kinds"] = [dict(k, has_future=k["has_future"] or k["kind"] in covered) for k in base.get("kinds", [])]
        if not stale and base.get("status") == "unconfigured":
            out["status"], out["error"] = "ok", None
        elif stale and base.get("status") == "unconfigured":
            out["error"] = f"일정 소진 — events.py 갱신 필요: {', '.join(stale)}"
    if c.error and not cache:
        out["econ"]["note"] = "외부 지표 일정을 받지 못했습니다. 기본 일정(FOMC·CPI·고용)만 표시합니다."
    return out


# ------------------------------------------------------------------ 명령줄: 점검·수동 입력
def _fmt_event(e: dict) -> str:
    t = datetime.fromtimestamp(e["ts"] / 1000, ZoneInfo(KST)).strftime("%m-%d %H:%M")
    return f"{t} KST  [{e['impact']:6s}] {e['name']}  이전 {e['previous'] or '—'} · 예상 {e['forecast'] or '—'} · 발표 {e['actual'] or '대기'}{'  (수동)' if e['manual'] else ''}"


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="경제지표 발표 일정 수집·점검·수동 입력")
    ap.add_argument("--data", default="data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="피드를 받아 오늘(한국 시간) 일정을 출력")
    sub.add_parser("list", help="캐시와 수동 입력을 합친 앞으로 일정 출력")
    a = sub.add_parser("add", help="피드에 없는 지표를 직접 추가")
    a.add_argument("name"); a.add_argument("when", help="한국 시간 'YYYY-MM-DD HH:MM'"); a.add_argument("--previous", default=""); a.add_argument("--forecast", default="")
    a.add_argument("--actual", default=""); a.add_argument("--impact", default="low", choices=["high", "medium", "low"])
    s = sub.add_parser("actual", help="이미 추가한 지표의 발표값 입력")
    s.add_argument("name"); s.add_argument("when"); s.add_argument("value")
    args = ap.parse_args(argv)
    c = Collector(args.data, network=True)
    now_ms = int(time.time() * 1000)
    if args.cmd == "check":
        r = c.refresh()
        print(f"받기 {'성공' if r['ok'] else '실패'} · 일정 {r['count']}건" + (f" · 오류: {'; '.join(r['errors'])}" if r["errors"] else ""))
        today = day_str(now_ms)
        for e in c.events():
            if day_str(e["ts"]) == today:
                print("  " + _fmt_event(e))
        if r["ok"] and not any(day_str(e["ts"]) == today for e in c.events()):
            print("  오늘(한국 시간) 일정이 피드에 없습니다. 'add' 로 직접 추가하세요.")
        return 0 if r["ok"] else 1
    if args.cmd == "list":
        for e in c.events():
            if e["ts"] >= now_ms - 86_400_000:
                print(_fmt_event(e))
        return 0
    path = c.manual_path
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if args.cmd == "add":
        rows.append({"name": args.name, "when": args.when, "previous": args.previous, "forecast": args.forecast, "actual": args.actual, "impact": args.impact})
    else:
        hit = [r for r in rows if r.get("name") == args.name and r.get("when") == args.when]
        if not hit:
            print("해당 항목이 없습니다. 이름과 시각이 추가할 때와 같아야 합니다.")
            return 1
        hit[0]["actual"] = args.value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {path} ({len(rows)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
