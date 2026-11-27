"""미국 주식(QQQ·나스닥 선물) 현황 수집(읽기 전용). 대시보드 분석 허브에 '지금 주식도 같이 움직이는가'를 보여 주기 위한 참고 자료다.
무료 공개 시세만 쓴다: 1순위 CNBC 시세, 실패하면 Nasdaq.com 시세(QQQ 만). 둘 다 브라우저 식별자(Safari)를 붙여야 응답한다(이 맥에서 확인).
Yahoo 는 429(요청 제한), Stooq 는 자바스크립트 확인 페이지가 나와서 쓰지 않는다. 최근 1시간 변화는 시세 API 에 없어서 받을 때마다 기록해 두었다가 계산한다.
공개 서비스는 예고 없이 막히거나 바뀔 수 있다. 실패하면 마지막 성공 값을 '오래됨'으로 표시하고 오류를 알린다. 매매에는 쓰지 않는다.
사용: python -m ena_bot.us_market check     (Mac 에서 실제로 받아지는지 확인)
      python -m ena_bot.us_market probe     (후보 출처별 상태와 응답 앞부분 진단)
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import threading
import time
from pathlib import Path

SYMBOLS = {"QQQ": ("QQQ(나스닥100 ETF)", "QQQ"), ".IXIC": ("나스닥 종합지수", ".IXIC")}
CNBC = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols={sym}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1"
NASDAQ = "https://api.nasdaq.com/api/quote/{sym}/info?assetclass=etf"
SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
UAS = (SAFARI, None)
PROBE_URLS = (
    "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=QQQ%7C.IXIC&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1",
    "https://api.nasdaq.com/api/quote/QQQ/info?assetclass=etf",
)
TTL = 60
BACKOFF_429 = 600                    # HTTP 429(요청 제한)를 받으면 이 시간(초) 동안 다시 시도하지 않는다
STALE_AFTER = 15 * 60
HIST_KEEP = 200


def http_get(url: str, ua: str | None = None, timeout: int = 15) -> str:
    """HTTP 상태 코드를 함께 확인한다(오류 메시지에 429·403·404 가 그대로 보이게)."""
    cmd = ["curl", "--http1.1", "-sL", "-m", str(timeout), "-w", "\n%{http_code}"] + (["-A", ua] if ua else []) + [url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        raise OSError(f"{timeout}초 안에 받지 못했습니다") from None
    body, _, code = r.stdout.rpartition("\n")
    if r.returncode != 0:
        raise OSError(f"curl 종료 코드 {r.returncode}")
    if not code.startswith("2"):
        raise OSError(f"HTTP {code}")
    return body


def probe(urls=None, fetch=None, width: int = 300) -> list[tuple[str, str, str]]:
    """후보 주소마다 (주소, 상태, 응답 앞부분)을 돌려준다. 실제로 어떤 출처가 받아지는지 확인하고 응답 모양을 보기 위한 진단용."""
    out = []
    for u in urls or PROBE_URLS:
        for ua in UAS:
            try:
                body = (fetch or http_get)(u, ua)
                out.append((u, "성공" + (" (Safari UA)" if ua else " (기본 UA)"), body[:width].replace("\n", " ⏎ ")))
                break
            except Exception as exc:  # noqa: BLE001
                out.append((u, f"실패 {exc}" + (" (Safari UA)" if ua else " (기본 UA)"), ""))
    return out


def _num(v):
    if v is None:
        return None
    t = str(v).replace(",", "").replace("$", "").replace("%", "").replace("+", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _iso_ms(t):
    from datetime import datetime
    for f in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return int(datetime.strptime(t, f).timestamp() * 1000)
        except (ValueError, TypeError):
            continue
    return None


def parse_cnbc(text: str, symbol: str) -> dict:
    j = json.loads(text)
    rows = ((j.get("FormattedQuoteResult") or {}).get("FormattedQuote")) or []
    it = next((r for r in rows if isinstance(r, dict) and str(r.get("symbol", "")).upper() == symbol.upper()), None)
    if not it:
        raise ValueError(f"CNBC 응답에 {symbol} 가 없습니다")
    last = _num(it.get("last"))
    if not last:
        raise ValueError("CNBC 응답에 현재가가 없습니다")
    pct = _num(it.get("change_pct"))
    if pct is None:
        ch = _num(it.get("change"))
        pct = ch / (last - ch) * 100 if ch is not None and last != ch else None
    if pct is None:
        prev = _num(it.get("previous_day_closing"))
        pct = (last / prev - 1) * 100 if prev else None
    if pct is None:
        raise ValueError("CNBC 응답에서 등락률을 읽지 못했습니다")
    return {"symbol": symbol, "last": last, "prev_close": last / (1 + pct / 100), "pct_day": pct, "ts": _iso_ms(it.get("last_time")) or int(time.time() * 1000), "source": "cnbc", "pct_1h": None}


def parse_nasdaq(text: str, symbol: str) -> dict:
    j = json.loads(text)
    pd_ = ((j.get("data") or {}).get("primaryData")) or {}
    last, pct = _num(pd_.get("lastSalePrice")), _num(pd_.get("percentageChange"))
    if not last or pct is None:
        raise ValueError("Nasdaq 응답에서 현재가·등락률을 읽지 못했습니다")
    if str(pd_.get("deltaIndicator", "")).lower() == "down" and pct > 0:
        pct = -pct                                            # 부호가 없는 표기 대비
    return {"symbol": symbol, "last": last, "prev_close": last / (1 + pct / 100), "pct_day": pct, "ts": int(time.time() * 1000), "source": "nasdaq.com", "pct_1h": None}


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
        chain = [(CNBC.format(sym=sym), parse_cnbc)] + ([(NASDAQ.format(sym=sym), parse_nasdaq)] if sym == "QQQ" else [])
        for url, parse in chain:
            for ua in UAS:
                try:
                    return parse(self.fetch(url, ua), sym)
                except Exception as exc:  # noqa: BLE001
                    errs.append(f"{parse.__name__[6:]}: {str(exc)[:40]}")
                    if "429" in str(exc):
                        break
        raise OSError(" / ".join(errs))

    def _hist_path(self):
        return self.path.with_name("us_market_hist.json")

    def _with_hour_change(self, items: list[dict], now_ms: int) -> None:
        """받을 때마다 (시각, 값)을 기록하고, 약 1시간 전(50~90분) 기록이 있으면 그 대비 변화를 채운다."""
        try:
            hist = json.loads(self._hist_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hist = {}
        for x in items:
            h = hist.setdefault(x["symbol"], [])
            if not h or now_ms - h[-1][0] >= 50_000:
                h.append([now_ms, x["last"]])
            del h[:-HIST_KEEP]
            ref = [v for t, v in h if 3_000_000 <= now_ms - t <= 5_400_000]
            if ref and x.get("source") != "stale":
                x["pct_1h"] = (x["last"] / ref[0] - 1) * 100         # 가장 오래된 후보(약 1시간 전)
        try:
            self._hist_path().write_text(json.dumps(hist), encoding="utf-8")
        except OSError:
            pass

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
            self._with_hour_change(items, int(self.clock() * 1000))
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
            wait = BACKOFF_429 if "429" in (self.error or "") else TTL
            if self.clock() - self._last_try < wait:
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
    ap.add_argument("cmd", choices=["check", "probe"])
    ap.add_argument("--data", default="data")
    ap.add_argument("--full", action="store_true", help="probe: 응답을 길게(2500자) 보여 줌")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        for u, st, head in probe(width=2500 if a.full else 300):
            print(f"{st}\n  {u}" + (f"\n  응답: {head}" if head else ""))
        return 0
    c = Collector(a.data, network=True)
    r = c.refresh()
    for x in c.load().get("items", []):
        h = "—" if x.get("pct_1h") is None else f"{x['pct_1h']:+.2f}%"
        print(f"{x['symbol']:5s} 현재 {x['last']:.2f} · 전일 종가 {x['prev_close']:.2f} · 오늘 {x['pct_day']:+.2f}% · 최근 1시간 {h} · 출처 {x['source']}")
    print("결과:", "성공" if r["ok"] else f"일부/전체 실패 — {r['error']}")
    return 0 if r["items"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
