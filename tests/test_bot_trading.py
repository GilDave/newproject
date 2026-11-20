import hashlib
import hmac
import json
import os
import stat
import time
from pathlib import Path

import pytest

from solomon import exchange_keys as K
from solomon import trading_mode as M

from test_botlab import FakeRun, make_bot  # noqa: F401
from test_dashboard import Env, sleeper

KEY, SECRET = "AbCd1234EfGh5678", "sEcReT9999zzzzYYYY0000"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- the key vault
def test_save_writes_env_600_keeps_other_lines_and_backs_up(tmp_path):
    (tmp_path / ".env").write_text("# my bot\nTELEGRAM_TOKEN=abc\nBYBIT_API_KEY=old_key_value_1\nOTHER=1\n")
    st = K.save(tmp_path, "bybit", KEY, SECRET)
    text = (tmp_path / ".env").read_text()
    assert "# my bot" in text and "TELEGRAM_TOKEN=abc" in text and "OTHER=1" in text
    assert f"BYBIT_API_KEY={KEY}" in text and f"BYBIT_API_SECRET={SECRET}" in text and text.count("BYBIT_API_KEY") == 1
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    baks = list(tmp_path.glob(".env.bak-*"))
    assert baks and "old_key_value_1" in baks[0].read_text() and stat.S_IMODE(os.stat(baks[0]).st_mode) == 0o600
    assert st["key_set"] and st["secret_set"] and st["key_tail"] == KEY[-4:]


def test_status_never_contains_a_secret_or_the_whole_key(tmp_path):
    K.save(tmp_path, "bybit", KEY, SECRET)
    blob = json.dumps(K.status(tmp_path))
    assert SECRET not in blob and KEY not in blob and KEY[-4:] in blob


@pytest.mark.parametrize("key,secret", [("short", SECRET), (KEY, "x y z 12345678"), (KEY, KEY), ('"quoted_key_1234"', SECRET), ("", ""), (KEY + "\n", "no good!!!!")])
def test_bad_values_are_refused_and_nothing_is_written(tmp_path, key, secret):
    with pytest.raises(K.KeyError_):
        K.save(tmp_path, "bybit", key, secret)
    assert not (tmp_path / ".env").exists()


def test_remove_and_unknown_exchange_and_lookalike_variables(tmp_path):
    (tmp_path / ".env").write_text("BYBIT_KEY=legacy_name_value\n")
    assert K.status(tmp_path)["bybit"]["others"] == ["BYBIT_KEY"]
    K.save(tmp_path, "binance", KEY, SECRET)
    K.remove(tmp_path, "binance")
    assert "BINANCE" not in (tmp_path / ".env").read_text() and "BYBIT_KEY" in (tmp_path / ".env").read_text()
    with pytest.raises(K.KeyError_):
        K.save(tmp_path, "kraken", KEY, SECRET)
    assert K.status(tmp_path)["binance"]["bot_supported"] is False and K.status(tmp_path)["bybit"]["bot_supported"] is True


# ---------------------------------------------------------------- the one read-only check
def test_bybit_check_signs_correctly_and_flags_a_withdraw_permission():
    seen = {}

    def http(url, headers, timeout=15):
        seen.update(url=url, headers=headers)
        return 200, {"retCode": 0, "result": {"readOnly": 0, "ips": ["*"], "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": ["AccountTransfer", "Withdraw"]}}}

    r = K.verify("bybit", KEY, SECRET, http=http, now=lambda: 1_700_000_000.0)
    ts = "1700000000000"
    assert seen["url"] == "https://api.bybit.com/v5/user/query-api"
    assert seen["headers"]["X-BAPI-SIGN"] == hmac.new(SECRET.encode(), (ts + KEY + "5000").encode(), hashlib.sha256).hexdigest()
    assert r["ok"] and r["withdraw"] is True and r["can_trade"] is True and r["ip_restricted"] is False
    assert KEY not in json.dumps(r) and SECRET not in json.dumps(r)
    ok = K.verify("bybit", KEY, SECRET, http=lambda u, h, timeout=15: (200, {"retCode": 0, "result": {"readOnly": 1, "ips": ["1.2.3.4"], "permissions": {"ContractTrade": []}}}))
    assert ok["ok"] and ok["withdraw"] is False and ok["read_only"] is True and ok["ip_restricted"] is True
    assert K.verify("bybit", KEY, SECRET, testnet=True, http=lambda u, h, timeout=15: (seen.update(u=u) or (200, {"retCode": 0, "result": {}})))["testnet"] is True and "testnet" in seen["u"]


def test_binance_check_and_failures():
    r = K.verify("binance", KEY, SECRET, http=lambda u, h, timeout=15: (200, {"enableWithdrawals": False, "ipRestrict": True, "enableSpotAndMarginTrading": True, "enableFutures": False}))
    assert r["ok"] and r["withdraw"] is False and r["ip_restricted"] is True and r["can_trade"] is True
    bad = K.verify("binance", KEY, SECRET, http=lambda u, h, timeout=15: (401, {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}))
    assert not bad["ok"] and "받아주지 않았습니다" in bad["error"]
    assert not K.verify("bybit", KEY, SECRET, http=lambda u, h, timeout=15: (200, {"retCode": 10003, "retMsg": "API key is invalid."}))["ok"]

    def boom(u, h, timeout=15):
        raise OSError("down")

    assert "연결하지 못했습니다" in K.verify("bybit", KEY, SECRET, http=boom)["error"]


# ---------------------------------------------------------------- the switch
def fake_bot(root, live_engine=False):
    bot = make_bot(root)
    (bot / "config.json").write_text(json.dumps({"symbol": "ENAUSDT", "mode": "paper", "live_enabled": False, "news": {"enabled": True, "apply": False}}))
    (bot / "ena_bot" / "trend_engine.py").write_text("class LiveTrendBroker: pass\n" if live_engine else "class PaperTrendBroker: pass\n")
    return bot


GOOD_HEALTH = {"ok": True, "alive": True, "errors_24h": 0, "paper_days": 45.0}


def fresh_check(now, withdraw=False):
    return {"bybit": {"ok": True, "checked_at": now - 60, "withdraw": withdraw, "key_tail": "5678"}}


def test_state_explains_why_live_is_locked(home):
    bot = fake_bot(home)
    st = M.state(bot, {"ok": True, "alive": True, "errors_24h": 0, "paper_days": 12}, {}, time.time())
    assert st["mode"] == "paper" and not st["is_live"] and not st["can_go_live"]
    assert {"engine", "days", "keys"} <= {c["id"] for c in st["checks"] if not c["ok"]}


def test_live_is_refused_while_any_automatic_check_fails_and_config_is_untouched(home):
    bot = fake_bot(home)
    before = (bot / "config.json").read_text()
    with pytest.raises(M.ModeError) as e:
        M.set_live(bot, M.CONFIRM_PHRASE, [m[0] for m in M.MANUAL_CHECKS], GOOD_HEALTH, fresh_check(time.time()), time.time())
    assert e.value.status == 409 and any("실거래 브로커" in b for b in e.value.blockers)
    assert (bot / "config.json").read_text() == before and not list(bot.glob("config.json.bak-*"))


def test_live_needs_manual_checks_and_the_exact_phrase_then_writes_with_a_backup(home):
    bot = fake_bot(home, live_engine=True)
    now = time.time()
    all_manual = [m[0] for m in M.MANUAL_CHECKS]
    with pytest.raises(M.ModeError) as e:
        M.set_live(bot, M.CONFIRM_PHRASE, all_manual[:2], GOOD_HEALTH, fresh_check(now), now)
    assert e.value.status == 400 and e.value.blockers
    with pytest.raises(M.ModeError) as e:
        M.set_live(bot, "yes", all_manual, GOOD_HEALTH, fresh_check(now), now)
    assert e.value.status == 400 and "확인 문구" in e.value.message
    with pytest.raises(M.ModeError):  # a key that can withdraw blocks it even with everything else right
        M.set_live(bot, M.CONFIRM_PHRASE, all_manual, GOOD_HEALTH, fresh_check(now, withdraw=True), now)
    with pytest.raises(M.ModeError):  # an old key check does not count
        M.set_live(bot, M.CONFIRM_PHRASE, all_manual, GOOD_HEALTH, {"bybit": {"ok": True, "checked_at": now - 3 * 86400, "withdraw": False}}, now)
    M.set_live(bot, M.CONFIRM_PHRASE, all_manual, GOOD_HEALTH, fresh_check(now), now)
    cfg = json.loads((bot / "config.json").read_text())
    assert cfg["mode"] == "live" and cfg["live_enabled"] is True and cfg["news"] == {"enabled": True, "apply": False} and cfg["symbol"] == "ENAUSDT"
    assert json.loads(next(bot.glob("config.json.bak-*")).read_text())["mode"] == "paper"


def test_going_back_to_paper_is_always_one_step(home):
    bot = fake_bot(home)
    cfg = json.loads((bot / "config.json").read_text())
    cfg.update(mode="live", live_enabled=True)
    (bot / "config.json").write_text(json.dumps(cfg))
    assert M.state(bot, None, {}, time.time())["is_live"]
    M.set_paper(bot)
    after = json.loads((bot / "config.json").read_text())
    assert after["mode"] == "paper" and after["live_enabled"] is False


# ---------------------------------------------------------------- through the dashboard
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sleeper.calls.clear()
    e = Env(tmp_path)
    e.app.botlab._run = FakeRun()
    yield e
    e.close()


def connect(env, tmp_path):
    bot = fake_bot(tmp_path)
    assert env.call("POST", "/api/botlab/config", {"bot_dir": str(bot)})[0] == 200
    return bot


def test_secrets_go_into_env_and_never_come_back_in_any_response(env, tmp_path, monkeypatch):
    bot = connect(env, tmp_path)
    st, body, _ = env.call("POST", "/api/botlab/keys", {"action": "save", "exchange": "bybit", "key": KEY, "secret": SECRET})
    assert st == 200 and f"BYBIT_API_SECRET={SECRET}" in (bot / ".env").read_text()
    monkeypatch.setattr(K, "verify", lambda ex, k, sec, testnet=False: {"exchange": ex, "ok": True, "withdraw": False, "read_only": False, "can_trade": True,
                                                                      "ip_restricted": False, "testnet": testnet, "checked_at": time.time(), "key_tail": k[-4:], "error": ""})
    st2, body2, _ = env.call("POST", "/api/botlab/keys", {"action": "verify", "exchange": "bybit"})
    assert st2 == 200 and body2["keys_check"]["bybit"]["ok"]
    for resp in (body, body2, env.call("GET", "/api/botlab")[1]):
        text = json.dumps(resp)
        assert SECRET not in text and KEY not in text
    assert SECRET not in (env.app.botlab.dir / "keys_check.json").read_text()
    for f in tmp_path.rglob("*.log"):
        assert SECRET not in f.read_text(errors="ignore")


def test_bad_key_input_is_a_400_and_unknown_actions_too(env, tmp_path):
    connect(env, tmp_path)
    assert env.call("POST", "/api/botlab/keys", {"action": "save", "exchange": "bybit", "key": "x", "secret": "y"})[0] == 400
    assert env.call("POST", "/api/botlab/keys", {"action": "explode", "exchange": "bybit"})[0] == 400
    assert env.call("POST", "/api/botlab/keys", {"action": "verify", "exchange": "bybit"})[0] == 400  # nothing stored yet
    assert env.call("POST", "/api/botlab/keys", {"action": "save", "exchange": "bybit", "key": KEY, "secret": SECRET}, token=None)[0] == 401


def test_the_switch_over_the_api_is_locked_today_but_paper_always_works(env, tmp_path):
    bot = connect(env, tmp_path)
    st, body, _ = env.call("GET", "/api/botlab")
    assert body["trading"]["mode"] == "paper" and body["trading"]["can_go_live"] is False and ".env" in body["recommended_protect"]
    assert "ena_bot/trend_engine.py" not in body["recommended_protect"] and "ena_bot/exchange.py" in body["recommended_protect"]
    st, body, _ = env.call("POST", "/api/botlab/mode", {"mode": "live", "confirm": M.CONFIRM_PHRASE, "checks": [m[0] for m in M.MANUAL_CHECKS]})
    assert st == 409 and "실거래" in body["error"]
    assert json.loads((bot / "config.json").read_text())["mode"] == "paper"
    assert env.call("POST", "/api/botlab/mode", {"mode": "paper"})[0] == 200
    assert env.call("POST", "/api/botlab/mode", {"mode": "nonsense"})[0] == 400


def test_the_tab_has_the_switch_the_key_menu_and_does_not_wipe_typing():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ("거래 모드", "openLiveDialog", "/api/botlab/keys", "/api/botlab/mode", "typing", "type = \"password\"", "페이퍼 전용 프리셋"):
        assert needle in html, needle


def test_the_monitor_card_is_really_on_the_bot_tab_and_is_fed_by_the_state(env, tmp_path):
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    assert html.count("봇 자체 모니터") == 1 and "ENA 트레이딩 모니터 열기" in html and "bot.monitor" in html
    assert env.call("GET", "/api/botlab")[1]["monitor"] is None  # not connected yet: no card
    connect(env, tmp_path)
    m = env.call("GET", "/api/botlab")[1]["monitor"]
    assert m["url"] == "http://127.0.0.1:8787/" and m["up"] in (True, False)
