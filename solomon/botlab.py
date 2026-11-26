"""Bot lab: keep a live trading bot's own shadow signals scored automatically, without ever touching the bot.

Everything here is read-only towards the bot: its database is opened with mode=ro, fresh prices are written to OUR folder,
the bot's code, config, services and data are never changed. Changing the bot is only ever done by a person applying a result.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from . import shadow

# Files no AI may change by default: order execution, risk limits, the live loop, credentials, data, service definitions.
DEFAULT_PROTECT = [".env", "*.env", "config.json", "data/", ".venv/", "*.plist",
                   "ena_bot/exchange.py", "ena_bot/risk.py", "ena_bot/config.py", "ena_bot/engine.py",
                   "ena_bot/trend_engine.py", "ena_bot/main.py", "ena_bot/store.py", "ena_bot/notify.py"]

# For a PAPER-only bot: the live path, credentials, settings, data and service files stay protected; the strategy engine can be worked on.
PAPER_PROTECT = [".env", "*.env", "config.json", "data/", "!data/research/", ".venv/", "*.plist",
                 "ena_bot/exchange.py", "ena_bot/risk.py", "ena_bot/config.py", "ena_bot/engine.py", "ena_bot/main.py", "ena_bot/store.py", "ena_bot/notify.py"]

DEFAULTS = {"bot_dir": "", "enabled": True, "interval_hours": 6.0, "fetch_days": 30,
            "auto_analyze": True, "analyze_hours": 24.0, "last_eval": 0.0, "last_analyze": 0.0, "last_analyze_error": ""}
ALIVE_WITHIN_HOURS = 9.0  # the trend loop wakes about every 4 hours
HISTORY_KEEP = 500


class BotLabError(Exception):
    pass


ENA_LABEL = re.compile(r"^com\.ena\.[A-Za-z0-9_.-]{1,40}$")
RESTART_ORDER = ("com.ena.report", "com.ena.ollama", "com.ena.dashboard", "com.ena.trend")  # the trading engine last: everything it depends on is already back


def detect(projects: list[str]) -> str:
    """A registered project that looks like the ena-bot (has an ena_bot package and its database)."""
    for p in projects:
        if (Path(p) / "ena_bot").is_dir() and (Path(p) / "data" / "bot.db").is_file():
            return p
    return ""


class BotLab:
    def __init__(self, state_dir: Path, run: Callable = subprocess.run, clock: Callable[[], float] = time.time):
        self.dir = Path(state_dir)
        self._run = run
        self._clock = clock
        self.running = False
        self.last_message = ""

    # ------------------------------------------------------------------ config
    @property
    def cfg_path(self) -> Path:
        return self.dir / "config.json"

    def config(self) -> dict:
        cfg = dict(DEFAULTS)
        try:
            cfg.update({k: v for k, v in json.loads(self.cfg_path.read_text(encoding="utf-8")).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        return cfg

    def save(self, cfg: dict) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.cfg_path)
        return cfg

    def update(self, body: dict) -> dict:
        cfg = self.config()
        if "bot_dir" in body:
            d = Path(str(body["bot_dir"])).expanduser().resolve()
            if not (d / "data" / "bot.db").is_file() or not (d / "ena_bot").is_dir():
                raise BotLabError("이 폴더에서 봇(ena_bot 폴더와 data/bot.db)을 찾지 못했습니다.")
            try:
                d.relative_to(Path.home().resolve())
            except ValueError:
                raise BotLabError("홈 폴더 안의 폴더만 지정할 수 있습니다.") from None
            cfg["bot_dir"] = str(d)
        for k in ("enabled", "auto_analyze"):
            if k in body:
                cfg[k] = bool(body[k])
        for k, lo, hi in (("interval_hours", 1, 72), ("analyze_hours", 6, 336), ("fetch_days", 10, 90)):
            if k in body:
                try:
                    v = float(body[k])
                except (TypeError, ValueError):
                    raise BotLabError(f"{k} 값이 숫자가 아닙니다.") from None
                if not lo <= v <= hi:
                    raise BotLabError(f"{k}는 {lo}~{hi} 사이여야 합니다.")
                cfg[k] = v
        return self.save(cfg)

    # ------------------------------------------------------------------ the bot's own state (read-only)
    def _python(self, bot_dir: Path) -> Optional[Path]:
        for rel in (".venv/bin/python", "venv/bin/python", "env/bin/python"):
            p = bot_dir / rel
            if p.is_file() and os.access(p, os.X_OK):
                return p
        return None

    def services(self) -> dict:
        try:
            out = self._run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return {"available": False, "items": []}
        items = []
        for line in (out.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[2].startswith("com.ena."):
                items.append({"label": parts[2], "running": parts[0].strip() not in ("-", ""), "last_exit": parts[1].strip()})
        return {"available": True, "items": sorted(items, key=lambda x: x["label"])}

    # An `ena_bot` process that launchd does not manage (started by hand, for example in a terminal). It is invisible in the
    # service list above yet may write the same bot.db: the old 3-minute engine (`run`) once did this for a week unnoticed.
    ENA_SUBCOMMANDS = ("run", "trend-run", "dashboard", "report", "trend", "trend-compare", "learn-train", "fetch", "backtest",
                       "walkforward", "synth", "resume", "hayes")
    WRITERS = {"run": "3분봉 엔진(v1)", "trend-run": "추세 엔진"}  # long-running writers of bot.db

    def stray_processes(self) -> dict:
        """Read-only. Returns {"available": bool, "items": [...]}: ena_bot processes that are not launchd services or their children."""
        try:
            ps = self._run(["ps", "-axww", "-o", "pid=,ppid=,etime=,command="], capture_output=True, text=True, timeout=10)
            lc = self._run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
            rows = []
            for line in (ps.stdout or "").splitlines():
                m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", line)
                if m:
                    rows.append((int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)))
            managed = set()
            for line in (lc.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and parts[2].startswith("com.ena.") and parts[0].strip().isdigit():
                    managed.add(int(parts[0]))
            kids: dict = {}
            for pid, ppid, _e, _c in rows:
                kids.setdefault(ppid, []).append(pid)
            stack = list(managed)
            while stack:  # the managed processes and everything they started
                for k in kids.get(stack.pop(), []):
                    if k not in managed:
                        managed.add(k)
                        stack.append(k)
            me = os.getpid()
            items = []
            for pid, _pp, etime, cmd in rows:
                toks = cmd.split()
                if pid in managed or pid == me or "-m" not in toks or "ena_bot" not in toks:
                    continue
                if toks[0].endswith("caffeinate"):
                    continue
                rest = toks[toks.index("ena_bot") + 1:]
                sub = next((t for t in rest if t in self.ENA_SUBCOMMANDS), "")
                items.append({"pid": pid, "command": sub or "?", "running_for": etime, "line": re.sub(r"\s+", " ", cmd)[-160:],
                              "writer": self.WRITERS.get(sub, "")})
            return {"available": True, "items": items}
        except Exception:  # a failing `ps` must never break the page
            return {"available": False, "items": []}

    def restart_services(self, labels: list, sleep: Callable[[float], None] = time.sleep) -> dict:
        """Restart the bot's launchd services (`launchctl kickstart -k`). Only labels `launchctl list` shows AND that start with com.ena. are
        accepted, so nothing else on the computer can be touched; the trading engine goes last."""
        if not isinstance(labels, list) or not labels or len(labels) > 8 or not all(isinstance(x, str) for x in labels):
            raise BotLabError("재시작할 서비스를 하나 이상 고르세요.")
        svc = self.services()
        if not svc["available"]:
            raise BotLabError("launchctl을 쓸 수 없습니다 (맥에서만 서비스를 재시작할 수 있습니다).")
        known = {i["label"] for i in svc["items"]}
        for lab in labels:
            if not ENA_LABEL.match(lab) or lab not in known:
                raise BotLabError(f"알 수 없는 서비스입니다: {lab}")
        chosen = sorted(set(labels), key=lambda x: RESTART_ORDER.index(x) if x in RESTART_ORDER else -1)
        uid = os.getuid()
        results = []
        for lab in chosen:
            try:
                res = self._run(["launchctl", "kickstart", "-k", f"gui/{uid}/{lab}"], capture_output=True, text=True, timeout=20)
                ok, err = res.returncode == 0, ((res.stderr or res.stdout or "").strip()[:200] if res.returncode else "")
            except (OSError, subprocess.SubprocessError) as exc:
                ok, err = False, str(exc)[:200]
            results.append({"label": lab, "ok": ok, "error": err})
        sleep(3.0)  # give them a moment, then look again
        after = {i["label"]: i["running"] for i in self.services().get("items", [])}
        for r in results:
            r["running"] = bool(after.get(r["label"]))
        self._monitor = (0.0, False)  # the 8787 page was cached for 10 seconds: look again
        return {"results": results, "monitor": bool(self.monitor().get("up"))}

    def health(self, bot_dir: str) -> dict:
        db = Path(bot_dir) / "data" / "bot.db"
        if not db.is_file():
            return {"ok": False, "error": "bot.db 없음"}
        try:
            con = shadow._open_ro(db)
            try:
                kinds = con.execute("select kind, count(*), max(ts_ms), min(ts_ms) from events group by kind").fetchall()
                now_ms = int(self._clock() * 1000)
                errs = con.execute("select count(*) from events where kind='error' and ts_ms > ?", (now_ms - 86_400_000,)).fetchone()[0]
            finally:
                con.close()
        except Exception as exc:  # a locked or odd database must never break the page
            return {"ok": False, "error": str(exc)[:200]}
        last = {k: int(t) for k, _, t, _f in kinds if t}
        first = {k: int(f) for k, _, _t, f in kinds if f}
        cyc = last.get("trend_cycle", 0)
        age_h = (self._clock() * 1000 - cyc) / 3_600_000 if cyc else None
        return {"ok": True, "kinds": [{"kind": k, "count": c, "last_ms": t, "first_ms": f} for k, c, t, f in sorted(kinds, key=lambda x: -x[1])],
                "paper_days": round((self._clock() * 1000 - first["trend_cycle"]) / 86_400_000, 1) if first.get("trend_cycle") else 0.0,
                "errors_24h": errs, "last_cycle_age_hours": round(age_h, 1) if age_h is not None else None,
                "alive": age_h is not None and age_h <= ALIVE_WITHIN_HOURS}

    _monitor: tuple = (0.0, False)

    def monitor(self, port: int = 8787) -> dict:
        """Is the bot's OWN read-only monitor page up? (A one-second look at 127.0.0.1; cached for 10 seconds.)"""
        import urllib.request

        now = self._clock()
        url = f"http://127.0.0.1:{port}/"
        if now - self._monitor[0] < 10:
            return {"url": url, "up": self._monitor[1]}
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                up = 200 <= r.status < 500
        except Exception:
            up = False
        self._monitor = (now, up)
        return {"url": url, "up": up}

    # ------------------------------------------------------------------ evaluation
    def _fetch(self, bot_dir: Path, days: float, csv_path: Path) -> str:
        """Fresh prices into OUR folder via the bot's own Python. Returns '' on success, else the reason. The old file is kept on failure."""
        py = self._python(bot_dir)
        if not py:
            return "봇의 파이썬(.venv)을 찾지 못했습니다"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=csv_path.parent, suffix=".csv")
        os.close(fd)
        try:
            res = self._run([str(py), "-W", "ignore", "-m", "ena_bot", "fetch", "--days", str(int(days)), "--out", tmp],
                            cwd=str(bot_dir), capture_output=True, text=True, timeout=600)
            if res.returncode != 0 or not Path(tmp).stat().st_size:
                return ((res.stderr or res.stdout or "").strip().splitlines() or ["가격 받기 실패"])[-1][:200]
            os.replace(tmp, csv_path)
            return ""
        except (OSError, subprocess.SubprocessError) as exc:
            return str(exc)[:200]
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def needed_days(self, db: Path, cap: float) -> float:
        """How many days of prices the scoring really needs: back to the oldest scored event plus its 24-hour horizon, never more than
        the configured cap. (Asking the exchange for 30 days every time is 80% wasted load on the same IP the bot itself uses.)"""
        try:
            con = shadow._open_ro(db)
            try:
                first = con.execute("select min(ts_ms) from events where kind in ('ml2_pred','news_score')").fetchone()[0]
            finally:
                con.close()
        except Exception:
            return float(cap)
        if not first:
            return float(min(cap, 5.0))
        return float(min(cap, max(5.0, (self._clock() * 1000 - first) / 86_400_000 + 2)))

    def evaluate(self) -> dict:
        cfg = self.config()
        if not cfg["bot_dir"]:
            raise BotLabError("봇 폴더가 설정되지 않았습니다.")
        if self.running:
            raise BotLabError("평가가 이미 진행 중입니다.")
        self.running = True
        try:
            bot = Path(cfg["bot_dir"])
            csv_path = self.dir / "ENAUSDT_3m.csv"
            err = self._fetch(bot, self.needed_days(bot / "data" / "bot.db", cfg["fetch_days"]), csv_path)
            have_prices = csv_path.is_file()
            db = bot / "data" / "bot.db"
            entry = {"ts": self._clock(), "fetch_error": err, "stale_prices": bool(err) and have_prices,
                     "signals": None, "filter": None, "error": ""}
            try:
                entry["signals"] = shadow.evaluate_bot_signals(db, csv_path if have_prices else None)
                filt = shadow.evaluate_bot_filter(db, csv_path if have_prices else None)
                entry["filter"] = filt if filt["events"] else None  # the old LLM filter writes nothing today
            except Exception as exc:
                entry["error"] = str(exc)[:300]
            hist = self.dir / "history"
            hist.mkdir(parents=True, exist_ok=True)
            (hist / f"{int(entry['ts'])}.json").write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            for old in sorted(hist.glob("*.json"))[:-HISTORY_KEEP]:
                old.unlink(missing_ok=True)
            cfg["last_eval"] = entry["ts"]
            self.save(cfg)
            self.last_message = ("평가를 마쳤습니다" + (f" (가격 받기 실패: {err} — 이전 가격으로 계산)" if err else "")) if not entry["error"] else f"평가 오류: {entry['error']}"
            return entry
        finally:
            self.running = False

    def history(self, limit: int = 200) -> list[dict]:
        out = []
        for f in sorted((self.dir / "history").glob("*.json"))[-limit:]:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return out

    def series(self, hist: list[dict]) -> list[dict]:
        pts = []
        for e in hist:
            s = e.get("signals") or {}
            ml = (s.get("ml2") or {}).get("overall") or {}
            nw = s.get("news") or {}
            pts.append({"ts": e["ts"], "ml_n": ml.get("n", 0), "ml_rate": ml.get("rate"), "waiting": s.get("waiting_for_price", 0),
                        "news_hi": (nw.get("high_risk") or {}).get("n", 0), "news_lo": (nw.get("low_risk") or {}).get("n", 0)})
        return pts

    # ------------------------------------------------------------------ scheduling
    def due_eval(self) -> bool:
        c = self.config()
        now = self._clock()
        near_bar_close = (now % 14400) < 240  # the bot fetches its own prices when a 4-hour bar closes: stay out of its way
        return bool(c["enabled"] and c["bot_dir"] and not self.running and not near_bar_close and now - c["last_eval"] >= c["interval_hours"] * 3600)

    def due_analyze(self) -> bool:
        c = self.config()
        return bool(c["auto_analyze"] and c["bot_dir"] and self._clock() - c["last_analyze"] >= c["analyze_hours"] * 3600)

    def mark_analyze(self, error: str = "") -> None:
        c = self.config()
        c["last_analyze"], c["last_analyze_error"] = self._clock(), error[:200]
        self.save(c)

    def analysis_focus(self, entry: Optional[dict]) -> str:
        s = (entry or {}).get("signals") or {}
        ml = (s.get("ml2") or {}).get("overall") or {}
        nw = s.get("news") or {}
        return ("이 프로젝트는 실계좌로 돌아가는 추세 추종 봇입니다. 코드를 바꾸지 말고 읽기만 하세요. "
                "예측(learn/learn2의 ml2 확률)과 뉴스 위험 점수(news)가 수익률 개선에 실제로 쓸모 있게 만들 방법을 찾아주세요: "
                "확률 보정, 특징, 표본 밖 검증 설계, 뉴스 점수 사용 방식. 각 제안은 어떤 숫자로 검증할지(표본 밖 워크포워드)를 함께 쓰세요. "
                f"참고 측정값: 예측 채점 {ml.get('n', 0)}건 적중 {ml.get('hits', 0)}건, 뉴스 높은 위험 {(nw.get('high_risk') or {}).get('n', 0)}건 / "
                f"낮은 위험 {(nw.get('low_risk') or {}).get('n', 0)}건 (표본이 30건 미만이면 결론이 아니므로 근거로 쓰지 마세요).")
