"""Phone alerts for a bot through Bark (an iPhone push app), sent by Solomon from the bot's read-only event log.

Nothing here touches the bot: it reads `events` (mode=ro), notices new trend cycles / errors / a silent bot and pushes a short message.
The Bark device key and the optional encryption key live in ~/.solomon/botlab/alerts_secret.json (mode 600) and are never returned,
logged or put in a message. With an encryption key the message is AES-128-CBC encrypted, so the relay server cannot read it
(set the same key, mode CBC, in the Bark app: Settings > Push encryption).
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import shadow

# ---------------------------------------------------------------- AES-128-CBC (encrypt only), standard library only
def _build_sbox() -> list[int]:
    sbox = [0] * 256
    p = q = 1
    rotl = lambda x, n: ((x << n) | (x >> (8 - n))) & 0xFF
    while True:
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        q &= 0xFF
        sbox[p] = q ^ rotl(q, 1) ^ rotl(q, 2) ^ rotl(q, 3) ^ rotl(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _build_sbox()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand_key(key: bytes) -> list[list[int]]:
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum((w[r * 4 + c] for c in range(4)), []) for r in range(11)]


def _encrypt_block(block: bytes, rks: list[list[int]]) -> bytes:
    s = [block[i] ^ rks[0][i] for i in range(16)]
    for rnd in range(1, 11):
        s = [_SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]  # ShiftRows (column-major state)
        if rnd != 10:
            out = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                out += [_xtime(a[0]) ^ (_xtime(a[1]) ^ a[1]) ^ a[2] ^ a[3], a[0] ^ _xtime(a[1]) ^ (_xtime(a[2]) ^ a[2]) ^ a[3],
                        a[0] ^ a[1] ^ _xtime(a[2]) ^ (_xtime(a[3]) ^ a[3]), (_xtime(a[0]) ^ a[0]) ^ a[1] ^ a[2] ^ _xtime(a[3])]
            s = out
        s = [s[i] ^ rks[rnd][i] for i in range(16)]
    return bytes(s)


def aes128_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("AES-128-CBC needs a 16-byte key and a 16-byte iv")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad  # PKCS7
    rks, prev, out = _expand_key(key), iv, b""
    for i in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = _encrypt_block(blk, rks)
        out += prev
    return out


# ---------------------------------------------------------------- Bark
DEFAULT_SERVER = "https://api.day.app"
KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
ENC_RE = re.compile(r"^[A-Za-z0-9]{16}$")


class AlertError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _http_post(url: str, payload: dict, timeout: float = 10.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except ValueError:
            return exc.code, {}


def build_payload(device_key: str, title: str, body: str, enc_key: str = "", group: str = "ena-bot", level: str = "active",
                  rand: Callable[[int], bytes] = os.urandom) -> dict:
    params = {"title": title, "body": body, "group": group, "level": level}
    if not enc_key:
        return {"device_key": device_key, **params}
    iv = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(16)) if rand is os.urandom else rand(16).hex()[:16]
    ct = aes128_cbc_encrypt(enc_key.encode(), iv.encode(), json.dumps(params, ensure_ascii=False).encode("utf-8"))
    return {"device_key": device_key, "ciphertext": base64.b64encode(ct).decode(), "iv": iv}


def send(server: str, device_key: str, title: str, body: str, enc_key: str = "", http: Callable = _http_post, **kw) -> tuple[bool, str]:
    try:
        code, data = http(server.rstrip("/") + "/push", build_payload(device_key, title, body, enc_key, **kw))
    except (OSError, ValueError) as exc:
        return False, f"Bark 서버에 연결하지 못했습니다 ({type(exc).__name__})"
    if code == 200 and data.get("code", 200) == 200:
        return True, ""
    return False, f"Bark가 받아주지 않았습니다: {str(data.get('message') or code)[:100]}"


# ---------------------------------------------------------------- config + secrets (kept apart, the secrets never leave)
DEFAULTS = {"enabled": False, "server": DEFAULT_SERVER, "signal": True, "signal_mode": "change", "error": True, "stale": True,
            # every period is the user's to set (ranges in LIMITS)
            "signal_step": 0.10,  # "change" mode: a message when the direction flips or the target exposure moves by at least this much
            "error_gap_min": 30.0, "stale_hours": 9.0, "stale_repeat_hours": 6.0,
            # what a message may contain (all the user's choice)
            "show_price": True, "show_entry": True, "show_pnl": True, "show_qty": False, "show_equity": True,
            "report": False, "report_minutes": 10.0, "quiet": False, "quiet_from": "23:00", "quiet_to": "07:00",
            "last_event_id": 0, "last_exposure": None, "last_error_alert": 0.0, "last_stale_alert": 0.0, "last_report": 0.0, "last_sent": 0.0, "last_failure": ""}
LIMITS = {"signal_step": (0.01, 1.0), "error_gap_min": (1, 1440), "stale_hours": (1, 72), "stale_repeat_hours": (1, 72), "report_minutes": (1, 1440)}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
BAR_SEC = 4 * 3600  # the trend engine decides when a 4-hour bar closes (UTC)


_LABEL = {"signal_step": "신호 알림 노출 변화 기준", "error_gap_min": "오류 알림 간격(분)", "stale_hours": "응답 없음 기준(시간)",
          "stale_repeat_hours": "응답 없음 반복 간격(시간)", "report_minutes": "상태 알림 주기(분)"}


def in_quiet(now: float, start: str, end: str) -> bool:
    """Is the local clock inside [start, end)? A window that crosses midnight (23:00 -> 07:00) works too."""
    t = time.localtime(now)
    cur = t.tm_hour * 60 + t.tm_min
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    a, b = sh * 60 + sm, eh * 60 + em
    if a == b:
        return False
    return a <= cur < b if a < b else (cur >= a or cur < b)


def _http_get_json(url: str, timeout: float = 6.0) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "solomon"}), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace") or "{}")


def fetch_price(symbol: str, get: Callable = _http_get_json) -> Optional[float]:
    """The last traded price from Bybit's PUBLIC ticker (no key, no account). None when it cannot be had."""
    if not re.match(r"^[A-Z0-9]{3,20}$", symbol or ""):
        return None
    try:
        d = get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")
        return float(d["result"]["list"][0]["lastPrice"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def read_monitor(port: int = 8787, get: Callable = _http_get_json) -> Optional[dict]:
    """What the bot's OWN monitor page shows, read from its local /api/state (read-only): current price, 24h change, the base account's
    position (accounts.base.qty / .entry) and the target exposure. None when the page is not up or looks different."""
    try:
        d = get(f"http://127.0.0.1:{port}/api/state", 2.5)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    base = (d.get("accounts") or {}).get("base") if isinstance(d.get("accounts"), dict) else None
    try:
        tk = d.get("ticker") or {}
        return {"symbol": d.get("symbol"), "price": float(tk["last"]) if tk.get("last") is not None else None,
                "pct24h": float(tk["pct24h"]) if tk.get("pct24h") is not None else None,
                "base": {"qty": float(base["qty"]), "entry": float(base["entry"])} if isinstance(base, dict) and "qty" in base and "entry" in base else None,
                "exposure": ((d.get("cards") or {}).get("trend") or {}).get("detail", {}).get("target_exposure")}
    except (KeyError, TypeError, ValueError):
        return None


def position_from_monitor(mon: Optional[dict]) -> Optional[dict]:
    b = (mon or {}).get("base")
    if not b:
        return None
    q = b["qty"]
    return {"key": "8787 모니터 화면의 데이터", "source": "monitor", "qty": q, "entry": b["entry"], "qty_field": "accounts.base.qty", "entry_field": "accounts.base.entry",
            "flat": q == 0 or b["entry"] <= 0, "side": "롱" if q > 0 else "숏" if q < 0 else "없음", "others": []}


class Alerts:
    def __init__(self, directory: Path, clock: Callable[[], float] = time.time, http: Callable = _http_post, price: Callable = fetch_price,
                 monitor: Optional[Callable] = None):
        self.dir = Path(directory)
        self._clock, self._http, self._price = clock, http, price
        self._monitor = monitor or (lambda: read_monitor())  # looked up at call time (tests and tools can replace read_monitor)
        self._last_price: Optional[float] = None

    @property
    def cfg_path(self) -> Path:
        return self.dir / "alerts.json"

    @property
    def secret_path(self) -> Path:
        return self.dir / "alerts_secret.json"

    def config(self) -> dict:
        cfg = dict(DEFAULTS)
        try:
            cfg.update({k: v for k, v in json.loads(self.cfg_path.read_text(encoding="utf-8")).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        return cfg

    def _save(self, cfg: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cfg_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cfg_path)

    def _secrets(self) -> dict:
        try:
            return json.loads(self.secret_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_secrets(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.secret_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.secret_path)
        os.chmod(self.secret_path, 0o600)

    def position(self, bot_dir: str | Path) -> Optional[dict]:
        """The open position exactly as the bot's own page shows it; the saved state in the database is the fallback."""
        return position_from_monitor(self._monitor()) or read_position(Path(bot_dir) / "data" / "bot.db")

    def public(self) -> dict:
        """What the page may see: switches and whether secrets exist (the last 4 characters of the device key), never the secrets."""
        c, s = self.config(), self._secrets()
        dk = s.get("device_key", "")
        return {**{k: c[k] for k in ("enabled", "server", "signal", "signal_mode", "error", "stale", "last_sent", "last_failure", "signal_step", "error_gap_min",
                                     "stale_hours", "stale_repeat_hours", "report", "report_minutes", "quiet", "quiet_from", "quiet_to", "last_report",
                                     "show_price", "show_entry", "show_pnl", "show_qty", "show_equity")},
                "key_set": bool(dk), "key_tail": dk[-4:] if len(dk) >= 8 else "", "enc_set": bool(s.get("enc_key"))}

    def update(self, body: dict) -> dict:
        c, s = self.config(), self._secrets()
        if "device_key" in body and str(body["device_key"]).strip():
            dk = str(body["device_key"]).strip()
            m = re.search(r"api\.day\.app/([A-Za-z0-9_\-]+)", dk)  # people paste the whole address Bark shows
            dk = m.group(1) if m else dk
            if not KEY_RE.match(dk):
                raise AlertError(400, "Bark 키 형식이 올바르지 않습니다. 앱에 보이는 주소에서 키만(또는 주소 전체를) 붙여넣으세요.")
            s["device_key"] = dk
        if "enc_key" in body:
            ek = str(body["enc_key"]).strip()
            if ek and not ENC_RE.match(ek):
                raise AlertError(400, "암호화 키는 영문·숫자 정확히 16자여야 합니다.")
            if ek:
                s["enc_key"] = ek
            elif body.get("clear_enc"):
                s.pop("enc_key", None)
        if "server" in body and str(body["server"]).strip():
            sv = str(body["server"]).strip().rstrip("/")
            if not sv.startswith("https://"):
                raise AlertError(400, "서버 주소는 https:// 로 시작해야 합니다.")
            c["server"] = sv
        for k in ("signal", "error", "stale", "report", "quiet", "show_price", "show_entry", "show_pnl", "show_qty", "show_equity"):
            if k in body:
                c[k] = bool(body[k])
        for k, (lo, hi) in LIMITS.items():
            if k in body:
                try:
                    v = float(body[k])
                except (TypeError, ValueError):
                    raise AlertError(400, f"{k} 값이 숫자가 아닙니다.") from None
                if not lo <= v <= hi:
                    raise AlertError(400, f"{_LABEL[k]}은(는) {lo:g}~{hi:g} 사이여야 합니다.")
                c[k] = v
        for k in ("quiet_from", "quiet_to"):
            if k in body:
                if not TIME_RE.match(str(body[k])):
                    raise AlertError(400, "조용한 시간은 HH:MM 형식(예: 23:00)이어야 합니다.")
                c[k] = str(body[k])
        if body.get("signal_mode") in ("change", "every"):
            c["signal_mode"] = body["signal_mode"]
        if "enabled" in body:
            if body["enabled"] and not s.get("device_key"):
                raise AlertError(400, "먼저 Bark 키를 저장하세요.")
            if body["enabled"] and not c["enabled"]:
                c["last_event_id"] = 0  # a fresh start: the first look only remembers where the log is, it sends nothing old
            c["enabled"] = bool(body["enabled"])
        self._save_secrets(s)
        self._save(c)
        return self.public()

    def forget(self) -> dict:
        self.secret_path.unlink(missing_ok=True)
        c = self.config()
        c["enabled"] = False
        self._save(c)
        return self.public()

    def _push(self, title: str, body: str) -> tuple[bool, str]:
        c, s = self.config(), self._secrets()
        if not s.get("device_key"):
            return False, "Bark 키가 없습니다"
        return send(c["server"], s["device_key"], title, body, s.get("enc_key", ""), http=self._http)

    def test(self) -> dict:
        ok, why = self._push("🔔 Solomon 테스트 알림", "이 알림이 보이면 아이폰 연결이 정상입니다." + (" (암호화됨)" if self._secrets().get("enc_key") else ""))
        c = self.config()
        c["last_failure"] = "" if ok else why
        if ok:
            c["last_sent"] = self._clock()
        self._save(c)
        if not ok:
            raise AlertError(502, why)
        return self.public()

    def _report(self, bot_dir: str | Path, c: dict, now: float, exposure: Optional[float], last_cycle: Optional[int]) -> tuple[str, str]:
        """The periodic status line: price now, what the engine last decided, when it decides next. (The signal only moves with the
        4-hour bars; a short period therefore mostly reports the price.)"""
        try:
            symbol = str(json.loads((Path(bot_dir) / "config.json").read_text(encoding="utf-8")).get("symbol") or "")
        except (OSError, ValueError):
            symbol = ""
        mon = self._monitor()
        px = (mon or {}).get("price") or self._price(symbol)  # the page's own price first: no outside call needed
        parts = []
        if px is not None:
            prev = self._last_price
            if c["show_price"]:
                parts.append(f"{symbol} {px:g}" + (f" ({(px / prev - 1) * 100:+.2f}%)" if prev else ""))
            self._last_price = px
        note = _price_note(c, position_from_monitor(mon) or read_position(Path(bot_dir) / "data" / "bot.db"), px)
        if note:
            parts.append(note[3:])
        if exposure is not None:
            parts.append(f"목표 노출 {exposure:+.2f} ({_side(exposure)})")
        if last_cycle:
            parts.append(f"마지막 판단 {(now * 1000 - last_cycle) / 3_600_000:.1f}시간 전")
        nxt = (int(now // BAR_SEC) + 1) * BAR_SEC - now
        parts.append(f"다음 판단 약 {int(nxt // 3600)}시간 {int(nxt % 3600 // 60)}분 뒤")
        return "🕒 ENA 상태", " · ".join(parts)

    # ------------------------------------------------------------------ the watcher (called every minute by the dashboard)
    def tick(self, bot_dir: str | Path) -> int:
        c = self.config()
        if not c["enabled"]:
            return 0
        db = Path(bot_dir) / "data" / "bot.db"
        try:
            con = shadow._open_ro(db)
        except Exception:
            return 0
        try:
            top = con.execute("select coalesce(max(id), 0) from events").fetchone()[0]
            if not c["last_event_id"]:  # first look: remember the present, send nothing from the past
                row = con.execute("select data from events where kind='trend_cycle' order by id desc limit 1").fetchone()
                c["last_event_id"], c["last_exposure"] = top, _exposure(row[0]) if row else None
                self._save(c)
                return 0
            rows = con.execute("select id, ts_ms, kind, data from events where id > ? and kind in ('trend_cycle','error') order by id", (c["last_event_id"],)).fetchall()
            last_cycle = con.execute("select max(ts_ms) from events where kind='trend_cycle'").fetchone()[0]
        except Exception:
            return 0
        finally:
            con.close()
        now = self._clock()
        msgs: list[tuple[str, str]] = []
        exposure = c["last_exposure"]
        for _id, ts, kind, raw in rows:
            if kind == "trend_cycle" and c["signal"]:
                cur = _exposure(raw)
                if cur is None:
                    continue
                if _worth_telling(exposure, cur, c["signal_mode"], c["signal_step"]):
                    mon = self._monitor()
                    msgs.append(_signal_text(exposure, cur, raw, c, position_from_monitor(mon) or read_position(db), (mon or {}).get("price")))
                exposure = cur
            elif kind == "error" and c["error"] and now - c["last_error_alert"] >= c["error_gap_min"] * 60:
                msgs.append(("⚠ ENA 봇 오류", _error_text(raw)))
                c["last_error_alert"] = now
        if c["stale"] and last_cycle and (now * 1000 - last_cycle) / 3_600_000 > c["stale_hours"] and now - c["last_stale_alert"] >= c["stale_repeat_hours"] * 3600:
            msgs.append(("🛑 ENA 봇 응답 없음", f"마지막 판단이 {(now * 1000 - last_cycle) / 3_600_000:.1f}시간 전입니다. 서비스(com.ena.trend)를 확인하세요."))
            c["last_stale_alert"] = now
        sent = 0
        if c["report"] and now - c["last_report"] >= c["report_minutes"] * 60 and not (c["quiet"] and in_quiet(now, c["quiet_from"], c["quiet_to"])):
            msgs.append(self._report(bot_dir, c, now, exposure, last_cycle))
            c["last_report"] = now
        if msgs:
            for title, body in msgs[-5:]:  # after an outage, at most the last five
                ok, why = self._push(title, body)
                if not ok:
                    c["last_failure"] = why
                    self._save(c)
                    return sent  # event ids stay where they were: the next minute tries again
                sent += 1
            c["last_sent"], c["last_failure"] = now, ""
        c["last_event_id"] = rows[-1][0] if rows else c["last_event_id"]
        c["last_exposure"] = exposure
        self._save(c)
        return sent


def _exposure(raw: str) -> Optional[float]:
    try:
        d = json.loads(raw)
        v = d.get("target_exposure")
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


_QTY = ("qty", "position", "pos", "size", "units", "quantity")
_ENTRY = ("avg_entry", "entry_price", "entry", "avg_price", "average_entry", "avg_px", "entry_px", "avg")


def _flat(d: dict, depth: int = 2, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[path] = float(v)
        elif isinstance(v, dict) and depth > 0:
            out.update(_flat(v, depth - 1, path + "."))
    return out


def read_position(db: str | Path) -> Optional[dict]:
    """The paper account's open position (quantity, average entry price), found in the bot's saved state (the `kv` table, read-only).

    The bot's field names are not known here, so every saved JSON state is looked at for a quantity-like and an entry-price-like number;
    states of shadow accounts are skipped. The result says WHERE it was found so a person can check it against the bot's own page."""
    try:
        con = shadow._open_ro(Path(db))
    except Exception:
        return None
    try:
        rows = con.execute("select key, value from kv").fetchall()
    except Exception:
        return None
    finally:
        con.close()
    found = []
    for key, val in rows:
        try:
            d = json.loads(val)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        flat = _flat(d)
        last = lambda path: path.rsplit(".", 1)[-1].lower()
        q = next((p for p in flat if last(p) in _QTY), None)
        e = next((p for p in flat if last(p) in _ENTRY and flat[p] > 0), None)
        if q and e:
            found.append({"key": key, "qty": flat[q], "entry": flat[e], "qty_field": q, "entry_field": e})
    if not found:
        return None
    rank = lambda f: (any(w in f["key"].lower() for w in ("shadow", "btc", "ml")), not any(w in f["key"].lower() for w in ("paper", "trend", "account")), f["key"])
    found.sort(key=rank)
    best = found[0]
    return {**best, "source": "kv", "flat": best["qty"] == 0, "side": "롱" if best["qty"] > 0 else "숏" if best["qty"] < 0 else "없음", "others": [f["key"] for f in found[1:4]]}


def _price_note(c: dict, pos: Optional[dict], px: Optional[float]) -> str:
    """The position part of a message, following the user's switches: ' · 숏 평균 진입가 0.11445 (+1.2%) · 수량 6,082'."""
    if not pos or pos["flat"] or not (c["show_entry"] or c["show_pnl"] or c["show_qty"]):
        return ""
    parts = []
    entry, q = pos["entry"], pos["qty"]
    if c["show_entry"]:
        parts.append(f"{pos['side']} 평균 진입가 {entry:g}")
    if c["show_pnl"] and px:
        pnl = (px - entry) / entry * (1 if q > 0 else -1) * 100  # a short gains when the price falls
        parts.append(f"진입가 대비 {pnl:+.2f}%")
    if c["show_qty"]:
        parts.append(f"수량 {abs(q):,.0f}")
    return " · " + " · ".join(parts) if parts else ""


def _worth_telling(prev: Optional[float], cur: float, mode: str, step: float = 0.10) -> bool:
    if mode == "every" or prev is None:
        return True
    return (prev == 0) != (cur == 0) or (prev > 0) != (cur > 0) or abs(cur - prev) >= step


def _side(x: float) -> str:
    return "롱" if x > 0.02 else "숏" if x < -0.02 else "중립"


def _signal_text(prev: Optional[float], cur: float, raw: str, c: Optional[dict] = None, pos: Optional[dict] = None, px_now: Optional[float] = None) -> tuple[str, str]:
    c = c or DEFAULTS
    try:
        d = json.loads(raw)
    except ValueError:
        d = {}
    price, eq = d.get("price"), d.get("equity")
    head = f"목표 노출 {prev:+.2f} → {cur:+.2f}" if prev is not None else f"목표 노출 {cur:+.2f}"
    flip = prev is not None and _side(prev) != _side(cur)
    body = ((f"{_side(prev)} → {_side(cur)} 전환 · " if flip else "") + f"{head} ({_side(cur)})"
            + (f" · 신호 가격 {price}" if price is not None and c["show_price"] else "")
            + _price_note(c, pos, px_now if px_now else (float(price) if isinstance(price, (int, float)) else None))
            + (f" · 가상 계좌 {float(eq):.1f} USDT" if isinstance(eq, (int, float)) and c["show_equity"] else ""))
    return ("🔄 ENA 추세 방향 전환" if flip else "📈 ENA 추세 신호"), body


def _error_text(raw: str) -> str:
    try:
        text = str(json.loads(raw).get("error", raw))
    except ValueError:
        text = str(raw)
    if "10006" in text or "too many visits" in text.lower() or "rate limit" in text.lower():
        return "거래소 호출 한도 초과 (대개 일시적이며 봇이 다시 시도합니다) — " + text[:100]
    return text[:140]
