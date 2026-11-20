"""Exchange API keys, kept in the bot's own `.env` (the file the bot already reads) and never anywhere else.

Rules that hold everywhere in this module:
  * a secret is written to `.env` (mode 600, with a dated backup of the old file) and is NEVER returned, logged, sent to an AI or put in
    an event: what comes back is only whether it is set and the last 4 characters of the KEY (not the secret);
  * a key is checked with ONE read-only call; a key that can WITHDRAW funds is flagged as dangerous.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

EXCHANGES = {
    "bybit": {"label": "바이비트 (Bybit)", "key": "BYBIT_API_KEY", "secret": "BYBIT_API_SECRET", "bot_supported": True},
    "binance": {"label": "바이낸스 (Binance)", "key": "BINANCE_API_KEY", "secret": "BINANCE_API_SECRET", "bot_supported": False},
}
VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
BACKUPS_KEPT = 5


class KeyError_(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def env_path(bot_dir: str | Path) -> Path:
    return Path(bot_dir) / ".env"


def _read_lines(p: Path) -> list[str]:
    try:
        return p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _assign(line: str) -> Optional[tuple[str, str]]:
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
    if not m or line.lstrip().startswith("#"):
        return None
    v = m.group(2)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return m.group(1), v


def _write_env(bot_dir: Path, lines: list[str]) -> None:
    p = env_path(bot_dir)
    if p.exists():  # a dated copy of what was there, so a wrong edit is never final
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bak = p.with_name(f".env.bak-{stamp}")
        bak.write_bytes(p.read_bytes())
        os.chmod(bak, 0o600)
        for old in sorted(p.parent.glob(".env.bak-*"))[:-BACKUPS_KEPT]:
            old.unlink(missing_ok=True)
    tmp = p.with_name(".env.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def status(bot_dir: str | Path) -> dict:
    """Which keys are set (never their values). `others` = variables that look like this exchange's but have another name."""
    vals = {n: v for n, v in (_assign(l) or ("", "") for l in _read_lines(env_path(bot_dir))) if n}
    out = {}
    for ex, d in EXCHANGES.items():
        k, sec = vals.get(d["key"], ""), vals.get(d["secret"], "")
        others = sorted(n for n in vals if ex.upper() in n.upper() and n not in (d["key"], d["secret"]))
        out[ex] = {"label": d["label"], "key_name": d["key"], "secret_name": d["secret"], "key_set": bool(k), "secret_set": bool(sec),
                   "key_tail": k[-4:] if len(k) >= 8 else "", "others": others, "bot_supported": d["bot_supported"]}
    return out


def load(bot_dir: str | Path, exchange: str) -> Optional[tuple[str, str]]:
    """The stored key pair, for server-side use only (the caller must never pass it on)."""
    d = EXCHANGES[exchange]
    vals = {n: v for n, v in (_assign(l) or ("", "") for l in _read_lines(env_path(bot_dir))) if n}
    return (vals[d["key"]], vals[d["secret"]]) if vals.get(d["key"]) and vals.get(d["secret"]) else None


def save(bot_dir: str | Path, exchange: str, key: str, secret: str) -> dict:
    if exchange not in EXCHANGES:
        raise KeyError_(400, "알 수 없는 거래소입니다.")
    key, secret = (key or "").strip(), (secret or "").strip()
    if not VALUE_RE.match(key) or not VALUE_RE.match(secret):
        raise KeyError_(400, "키와 시크릿 형식이 올바르지 않습니다 (영문·숫자·-·_ 8~128자). 공백이나 따옴표가 섞이지 않았는지 확인하세요.")
    if key == secret:
        raise KeyError_(400, "키와 시크릿이 같은 값입니다. 서로 바꿔 넣지 않았는지 확인하세요.")
    d = EXCHANGES[exchange]
    lines = _read_lines(env_path(bot_dir))
    done = set()
    for i, line in enumerate(lines):
        a = _assign(line)
        if a and a[0] in (d["key"], d["secret"]):
            lines[i] = f"{a[0]}={key if a[0] == d['key'] else secret}"
            done.add(a[0])
    for name, val in ((d["key"], key), (d["secret"], secret)):
        if name not in done:
            lines.append(f"{name}={val}")
    _write_env(Path(bot_dir), lines)
    return status(bot_dir)[exchange]


def remove(bot_dir: str | Path, exchange: str) -> dict:
    if exchange not in EXCHANGES:
        raise KeyError_(400, "알 수 없는 거래소입니다.")
    d = EXCHANGES[exchange]
    lines = [l for l in _read_lines(env_path(bot_dir)) if (_assign(l) or ("",))[0] not in (d["key"], d["secret"])]
    _write_env(Path(bot_dir), lines)
    return status(bot_dir)[exchange]


# ---------------------------------------------------------------- one read-only check
def _http_get(url: str, headers: dict, timeout: float = 15.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except ValueError:
            return exc.code, {}


def verify(exchange: str, key: str, secret: str, testnet: bool = False, http: Callable = _http_get, now: Callable[[], float] = time.time) -> dict:
    """Ask the exchange what this key may do (read-only endpoint). Returns permissions and flags; never the key."""
    res = {"exchange": exchange, "ok": False, "error": "", "withdraw": None, "read_only": None, "can_trade": None, "ip_restricted": None,
           "testnet": bool(testnet), "checked_at": now(), "key_tail": key[-4:] if len(key) >= 8 else ""}
    ts = str(int(now() * 1000))
    try:
        if exchange == "bybit":
            host = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
            recv = "5000"
            sign = hmac.new(secret.encode(), (ts + key + recv).encode(), hashlib.sha256).hexdigest()
            code, data = http(host + "/v5/user/query-api", {"X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign})
            if code != 200 or data.get("retCode") != 0:
                res["error"] = f"바이비트가 키를 받아주지 않았습니다: {str(data.get('retMsg') or code)[:120]}"
                return res
            r = data.get("result") or {}
            perms = r.get("permissions") or {}
            res.update(ok=True, read_only=bool(r.get("readOnly")), withdraw="withdraw" in json.dumps(perms).lower(),
                       can_trade=bool((perms.get("ContractTrade") or []) or (perms.get("Spot") or [])),
                       ip_restricted=bool(r.get("ips")) and r.get("ips") != ["*"])
        elif exchange == "binance":
            if testnet:
                res["error"] = "바이낸스는 테스트넷 확인을 지원하지 않습니다."
                return res
            q = urllib.parse.urlencode({"timestamp": ts, "recvWindow": "5000"})
            sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            code, data = http(f"https://api.binance.com/sapi/v1/account/apiRestrictions?{q}&signature={sig}", {"X-MBX-APIKEY": key})
            if code != 200 or "code" in data:
                res["error"] = f"바이낸스가 키를 받아주지 않았습니다: {str(data.get('msg') or code)[:120]}"
                return res
            res.update(ok=True, withdraw=bool(data.get("enableWithdrawals")), ip_restricted=bool(data.get("ipRestrict")),
                       can_trade=bool(data.get("enableSpotAndMarginTrading") or data.get("enableFutures")), read_only=not bool(data.get("enableSpotAndMarginTrading") or data.get("enableFutures")))
        else:
            res["error"] = "알 수 없는 거래소입니다."
    except (OSError, ValueError) as exc:
        res["error"] = f"거래소에 연결하지 못했습니다: {type(exc).__name__}"
    return res
