"""미국 주식(QQQ·나스닥 선물) 현황 수집(읽기 전용). 대시보드 분석 허브에 '지금 주식도 같이 움직이는가'를 보여 주기 위한 참고 자료다.
무료 공개 시세만 쓴다: 1순위 Yahoo Finance 차트(5분봉, 장중 등락과 최근 1시간 변화까지), 실패하면 Stooq 지연 시세(일간 등락만).
공개 서비스는 예고 없이 막히거나 바뀔 수 있다. 실패하면 마지막 성공 값을 '오래됨'으로 표시하고 오류를 알린다. 매매에는 쓰지 않는다.
사용: python -m ena_bot.us_market check     (Mac 에서 실제로 받아지는지 확인)
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import threading
import time
from pathlib import Path

SYMBOLS = {"QQQ": ("QQQ(나스닥100 ETF)", "qqq.us"), "NQ=F": ("나스닥100 선물", "nq.f")}
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=1d"
STOOQ = "https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"
UAS = (None, "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")
TTL = 60
STALE_AFTER = 15 * 60


def http_get(url: str, ua: str | None = None, timeout: int = 15) -> str:
    cmd = ["curl", "--http1.1", "-fsSL", "-m", str(timeout)] + (["-A", ua] if ua else []) + [url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        raise OSError(f"{timeout}초 안에 받지 못했습니다") from None
    if r.returncode != 0:
        raise OSError(f"curl 종료 코드 {r.returncode}: {(r.stderr or '').strip()[:60]}")
    return r.stdout


def parse_yahoo(text: str, symbol: str) -> dict:
    j = json.loads(text)
    res = ((j.get("chart") or {}).get("result") or [None])[0]
    if not res:
        raise ValueError("Yahoo 응답에 결과가 없습니다")
    meta = res.get("meta") or {}
    ts = res.get("timestamp") or []
    cl = (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    pts = [(int(t), float(c)) for t, c in zip(ts, cl) if c is not None]
    last = meta.get("regularMarketPrice") or (pts[-1][1] if pts else None)
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not last or not prev:
        raise ValueError("Yahoo 응답에 현재가·전일 종가가 없습니다")
    t_last = int(meta.get("regularMarketTime") or (pts[-1][0] if pts else 0))
    out = {"symbol": symbol, "last": float(last), "prev_close": float(prev), "pct_day": (float(last) / float(prev) - 1) * 100, "ts": t_last * 1000, "source": "yahoo", "pct_1h": None}
    if pts:
        ref = [c for t, c in pts if t <= pts[-1][0] - 3600]
        if ref:
            out["pct_1h"] = (pts[-1][1] / ref[-1] - 1) * 100
    return out


def parse_stooq(text: str, symbol: str) -> dict:
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    if not rows:
        raise ValueError("Stooq 응답이 비어 있습니다")
    r = rows[0]
    try:
        o, c = float(r["Open"]), float(r["Close"])
    except (KeyError, ValueError):
        raise ValueError("Stooq 값을 읽지 못했습니다") from None
    return {"symbol": symbol, "last": c, "prev_close": o, "pct_day": (c / o - 1) * 100, "ts": int(time.time() * 1000), "source": "stooq(지연·시가 대비)", "pct_1h": None}


class Collector:
    def __init__(self, data_dir="data", fetch=http_get, clock=time.time, network: bool | None = None):
        self.path = Path(data_dir) / "us_market.json"
        self.fetch, self.clock = fetch, clock
        import os
        self.network = (not os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("ENA_US_NETWORK") == "1") if network is None else network
        self.error = ""
        self._thread = None
        self._lock = threading.Lock()
        self._last_try = 0.0

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _one(self, sym: str) -> dict:
        errs = []
        for ua in UAS:
            try:
                return parse_yahoo(self.fetch(YAHOO.format(sym=sym), ua), sym)
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc)[:50])
        try:
            return parse_stooq(self.fetch(STOOQ.format(sym=SYMBOLS[sym][1]), None), sym)
        except Exception as exc:  # noqa: BLE001
            errs.append(str(exc)[:50])
        raise OSError(" / ".join(errs))

    def refresh(self) -> dict:
        items, errs = [], []
        old = {x["symbol"]: x for x in self.load().get("items", [])}
        for sym in SYMBOLS:
            try:
                items.append(self._one(sym))
            except Exception as exc:  # noqa: BLE001
                errs.append(f"{sym}: {str(exc)[:90]}")
                if sym in old:
                    items.append(old[sym])                                  # 마지막 성공 값을 유지(오래됨으로 표시됨)
        self.error = "; ".join(errs)
        if items:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"fetched_at": int(self.clock() * 1000), "items": items, "error": self.error}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        return {"ok": bool(items) and not errs, "items": len(items), "error": self.error}

    def poke(self) -> None:
        if not self.network:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self.clock() - self._last_try < TTL:
                return
            self._last_try = self.clock()
            self._thread = threading.Thread(target=self.refresh, daemon=True)
            self._thread.start()

    def payload(self) -> dict:
        self.poke()
        c = self.load()
        now = int(self.clock() * 1000)
        items = []
        for x in c.get("items", []):
            items.append({**x, "label": SYMBOLS.get(x["symbol"], (x["symbol"], ""))[0], "age_sec": max(0, (now - int(x.get("ts") or 0)) // 1000)})
        return {"ok": bool(items), "items": items, "fetched_at": c.get("fetched_at"), "error": c.get("error") or self.error,
                "note": "무료 공개 시세라 지연되거나 끊길 수 있습니다. 장이 닫혀 있으면 마지막 값이 그대로 보입니다. 참고용입니다."}


COLLECTOR = Collector()


def configure(data_dir) -> None:
    global COLLECTOR
    COLLECTOR = Collector(data_dir)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--data", default="data")
    a = ap.parse_args(argv)
    c = Collector(a.data, network=True)
    r = c.refresh()
    for x in c.load().get("items", []):
        h = "—" if x.get("pct_1h") is None else f"{x['pct_1h']:+.2f}%"
        print(f"{x['symbol']:5s} 현재 {x['last']:.2f} · 전일 종가 {x['prev_close']:.2f} · 오늘 {x['pct_day']:+.2f}% · 최근 1시간 {h} · 출처 {x['source']}")
    print("결과:", "성공" if r["ok"] else f"일부/전체 실패 — {r['error']}")
    return 0 if r["items"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
