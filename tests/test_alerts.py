import base64
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

from solomon import alerts as A

from test_botlab import FakeRun, make_bot  # noqa: F401
from test_dashboard import Env, sleeper

@pytest.fixture(autouse=True)
def no_real_monitor(monkeypatch):
    monkeypatch.setattr(A, "read_monitor", lambda port=8787, get=None: None)  # never talk to a real 8787 from the tests
    monkeypatch.setitem(A.DEFAULTS, "style", "detail")  # the older tests check the long message; the short one has its own tests below


DEVICE = "AbCdEfGh12345678XyZ"
ENC = "0123456789abcdef"


# ---------------------------------------------------------------- the cipher (checked against the standard's own vector and OpenSSL)
def test_aes_matches_the_fips_197_vector_and_openssl():
    key = bytes(range(16))
    assert A._encrypt_block(bytes.fromhex("00112233445566778899aabbccddeeff"), A._expand_key(key)).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"
    assert A._SBOX[0x53] == 0xED and A._SBOX[0x00] == 0x63
    data = "📈 ENA 추세 신호 목표 노출 -0.48 → -0.31".encode()
    mine = A.aes128_cbc_encrypt(ENC.encode(), b"abcdefghijklmnop", data)
    if shutil.which("openssl"):
        out = subprocess.run(["openssl", "enc", "-aes-128-cbc", "-K", ENC.encode().hex(), "-iv", b"abcdefghijklmnop".hex()], input=data, capture_output=True).stdout
        assert out == mine
    assert len(mine) % 16 == 0 and len(mine) > len(data)
    with pytest.raises(ValueError):
        A.aes128_cbc_encrypt(b"short", b"abcdefghijklmnop", b"x")


def test_payload_is_plain_without_a_key_and_encrypted_with_one():
    plain = A.build_payload(DEVICE, "제목", "내용")
    assert plain["device_key"] == DEVICE and plain["title"] == "제목" and "ciphertext" not in plain
    enc = A.build_payload(DEVICE, "제목", "비밀 내용", ENC)
    assert "title" not in enc and "비밀" not in json.dumps(enc, ensure_ascii=False) and len(enc["iv"]) == 16
    raw = base64.b64decode(enc["ciphertext"])
    if shutil.which("openssl"):
        out = subprocess.run(["openssl", "enc", "-d", "-aes-128-cbc", "-K", ENC.encode().hex(), "-iv", enc["iv"].encode().hex()], input=raw, capture_output=True).stdout
        assert json.loads(out.decode())["body"] == "비밀 내용"  # a receiver with the key reads it
    assert A.build_payload(DEVICE, "t", "b", ENC)["iv"] != A.build_payload(DEVICE, "t", "b", ENC)["iv"]  # a fresh iv every time


def test_send_reports_success_and_failures_without_leaking_the_key():
    ok, why = A.send("https://api.day.app", DEVICE, "t", "b", http=lambda u, p: (200, {"code": 200, "message": "success"}))
    assert ok and why == ""
    seen = {}
    A.send("https://api.day.app/", DEVICE, "t", "b", http=lambda u, p: seen.update(u=u) or (200, {}))
    assert seen["u"] == "https://api.day.app/push"
    ok, why = A.send("https://api.day.app", DEVICE, "t", "b", http=lambda u, p: (400, {"message": "failed to get device token"}))
    assert not ok and "받아주지 않았습니다" in why and DEVICE not in why

    def boom(u, p):
        raise OSError("down")

    assert "연결하지 못했습니다" in A.send("https://api.day.app", DEVICE, "t", "b", http=boom)[1]


# ---------------------------------------------------------------- config and secrets
def make(tmp_path, http=None, now=None):
    sent = []
    http = http or (lambda u, p: (sent.append(p) or (200, {"code": 200})))
    clock = (lambda: now[0]) if now else time.time
    return A.Alerts(tmp_path / "lab", clock=clock, http=http), sent


def test_secrets_are_stored_apart_with_600_and_never_returned(tmp_path):
    a, _ = make(tmp_path)
    pub = a.update({"device_key": "https://api.day.app/" + DEVICE + "/title/body", "enc_key": ENC})
    assert pub["key_set"] and pub["enc_set"] and pub["key_tail"] == DEVICE[-4:]
    assert DEVICE not in json.dumps(pub) and ENC not in json.dumps(pub)
    assert stat.S_IMODE(os.stat(a.secret_path).st_mode) == 0o600
    assert DEVICE in a.secret_path.read_text() and DEVICE not in a.cfg_path.read_text() and ENC not in a.cfg_path.read_text()  # the pasted address was reduced to the key
    for bad in ({"device_key": "no way!"}, {"enc_key": "tooshort"}, {"server": "http://plain.example"}, {"enabled": True} if False else {"server": "ftp://x"}):
        with pytest.raises(A.AlertError):
            a.update(bad)
    b, _ = make(tmp_path / "other")
    with pytest.raises(A.AlertError):
        b.update({"enabled": True})  # nothing to send with yet
    a.forget()
    assert not a.secret_path.exists() and not a.public()["key_set"] and not a.public()["enabled"]


def test_test_message_is_sent_encrypted_when_a_key_is_set(tmp_path):
    a, sent = make(tmp_path)
    a.update({"device_key": DEVICE, "enc_key": ENC})
    a.test()
    assert "ciphertext" in sent[0] and "title" not in sent[0]
    a2, sent2 = make(tmp_path / "p")
    a2.update({"device_key": DEVICE})
    a2.test()
    assert sent2[0]["title"].startswith("🔔")
    bad, _ = make(tmp_path / "q", http=lambda u, p: (400, {"message": "nope"}))
    bad.update({"device_key": DEVICE})
    with pytest.raises(A.AlertError):
        bad.test()
    assert "nope" in bad.public()["last_failure"]


# ---------------------------------------------------------------- the watcher
def put(bot, kind, data, ts=None):
    con = sqlite3.connect(bot / "data" / "bot.db")
    con.execute("insert into events(ts_ms, kind, data) values (?,?,?)", (ts or int(time.time() * 1000), kind, json.dumps(data)))
    con.commit()
    con.close()


def cycle(exp, price=0.118, eq=999.8):
    return {"ts": 1, "price": price, "target_exposure": exp, "equity": eq}


def test_first_look_sends_nothing_old_then_only_what_is_worth_telling(tmp_path):
    bot = make_bot(tmp_path / "b")
    a, sent = make(tmp_path)
    put(bot, "trend_cycle", cycle(-0.44))  # already in the log when alerts are switched on: never sent
    a.update({"device_key": DEVICE, "enabled": True})
    assert a.tick(bot) == 0 and not sent  # remembers where the log is
    put(bot, "trend_cycle", cycle(-0.45))
    put(bot, "trend_cycle", cycle(-0.46))  # a tiny move: not worth a push
    assert a.tick(bot) == 0 and not sent
    sent.clear()
    put(bot, "trend_cycle", cycle(0.30))  # the direction flipped
    n = a.tick(bot)
    assert n == 1 and "방향 전환" in sent[0]["title"] and "+0.30" in sent[0]["body"] and "숏" in sent[0]["body"] and "롱" in sent[0]["body"]
    sent.clear()
    put(bot, "trend_cycle", cycle(0.31))
    assert a.tick(bot) == 0 and not sent
    assert a.tick(bot) == 0  # the same events are never told twice


def test_every_mode_errors_and_silence(tmp_path):
    bot = make_bot(tmp_path / "b")
    now = [time.time()]
    a, sent = make(tmp_path, now=now)
    a.update({"device_key": DEVICE, "enabled": True, "signal_mode": "every"})
    a.tick(bot)
    put(bot, "trend_cycle", cycle(0.2), ts=int(now[0] * 1000))
    put(bot, "error", {"error": "Bybit error 10006: Too many visits"})
    put(bot, "error", {"error": "second one right after"})
    a.tick(bot)
    titles = [p["title"] for p in sent]
    assert any("추세 신호" in t for t in titles) and titles.count("⚠ ENA 봇 오류") == 1  # two errors, one message
    sent.clear()
    now[0] += 12 * 3600  # the bot has said nothing for 12 hours
    a.tick(bot)
    assert any("응답 없음" in p["title"] for p in sent)
    sent.clear()
    now[0] += 3600
    a.tick(bot)
    assert not any("응답 없음" in p["title"] for p in sent)  # not repeated every minute


def test_a_failed_send_is_retried_without_losing_the_event(tmp_path):
    bot = make_bot(tmp_path / "b")
    up = [False]
    sent = []

    def http(u, p):
        if not up[0]:
            return 502, {"message": "bad gateway"}
        sent.append(p)
        return 200, {"code": 200}

    a = A.Alerts(tmp_path / "lab", http=http)
    put(bot, "trend_cycle", cycle(-0.4))  # what the bot already said before alerts were switched on
    a.update({"device_key": DEVICE, "enabled": True})
    a.tick(bot)
    put(bot, "trend_cycle", cycle(-0.45))  # not worth telling
    put(bot, "trend_cycle", cycle(0.5))  # the direction flipped
    assert a.tick(bot) == 0 and "bad gateway" in a.public()["last_failure"]
    up[0] = True
    assert a.tick(bot) == 1 and a.public()["last_failure"] == ""  # told once it works again


def test_disabled_or_missing_db_does_nothing(tmp_path):
    a, sent = make(tmp_path)
    assert a.tick(tmp_path / "nowhere") == 0
    a.update({"device_key": DEVICE, "enabled": True})
    assert a.tick(tmp_path / "nowhere") == 0 and not sent


# ---------------------------------------------------------------- through the dashboard
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sleeper.calls.clear()
    e = Env(tmp_path)
    e.app.botlab._run = FakeRun()
    sent = []
    e.app.alerts._http = lambda u, p: (sent.append(p) or (200, {"code": 200}))
    e.sent = sent
    yield e
    e.close()


def test_endpoints_save_test_forget_and_never_echo_the_secrets(env, tmp_path):
    st, body, _ = env.call("POST", "/api/botlab/alerts", {"action": "save", "device_key": DEVICE, "enc_key": ENC})
    assert st == 200 and body["alerts"]["key_set"] and body["alerts"]["enc_set"]
    st, body, _ = env.call("POST", "/api/botlab/alerts", {"action": "test"})
    assert st == 200 and env.sent and "ciphertext" in env.sent[0]
    for resp in (body, env.call("GET", "/api/botlab")[1]):
        assert DEVICE not in json.dumps(resp) and ENC not in json.dumps(resp)
    assert env.call("POST", "/api/botlab/alerts", {"action": "save", "device_key": "bad key!"})[0] == 400
    assert env.call("POST", "/api/botlab/alerts", {"action": "nope"})[0] == 400
    assert env.call("POST", "/api/botlab/alerts", {"action": "test"}, token=None)[0] == 401
    assert env.call("POST", "/api/botlab/alerts", {"action": "forget"})[1]["alerts"]["key_set"] is False
    for f in tmp_path.rglob("*.log"):
        assert DEVICE not in f.read_text(errors="ignore") and ENC not in f.read_text(errors="ignore")


def test_the_card_is_on_the_bot_tab():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ("🔔 아이폰 알림 (Bark)", "/api/botlab/alerts", "테스트 알림 보내기", "무작위 암호화 키 만들기", "crypto.getRandomValues"):
        assert needle in html, needle


# ---------------------------------------------------------------- periods are the user's to set (v1.12.1)
def test_periods_have_ranges_and_quiet_times_must_be_clock_times(tmp_path):
    a, _ = make(tmp_path)
    pub = a.update({"report": True, "report_minutes": 10, "error_gap_min": 5, "stale_hours": 2, "stale_repeat_hours": 3, "signal_step": 0.2, "quiet": True, "quiet_from": "22:30", "quiet_to": "06:15"})
    assert pub["report"] and pub["report_minutes"] == 10 and pub["error_gap_min"] == 5 and pub["signal_step"] == 0.2 and pub["quiet_from"] == "22:30"
    for bad in ({"report_minutes": 0}, {"report_minutes": 2000}, {"report_minutes": "x"}, {"stale_hours": 100}, {"signal_step": 5}, {"quiet_from": "25:00"}, {"quiet_to": "7pm"}):
        with pytest.raises(A.AlertError):
            a.update(bad)
    assert a.public()["report_minutes"] == 10  # a refused change leaves the old value


def test_quiet_window_works_across_midnight():
    t = lambda h, m: time.mktime((2026, 11, 20, h, m, 0, 0, 0, -1))
    assert A.in_quiet(t(23, 30), "23:00", "07:00") and A.in_quiet(t(3, 0), "23:00", "07:00") and not A.in_quiet(t(12, 0), "23:00", "07:00")
    assert not A.in_quiet(t(7, 0), "23:00", "07:00") and A.in_quiet(t(23, 0), "23:00", "07:00")
    assert A.in_quiet(t(13, 0), "12:00", "14:00") and not A.in_quiet(t(15, 0), "12:00", "14:00") and not A.in_quiet(t(5, 0), "08:00", "08:00")


def test_the_status_report_goes_out_every_period_with_price_and_next_decision(tmp_path):
    bot = make_bot(tmp_path / "b")
    (bot / "config.json").write_text(json.dumps({"symbol": "ENAUSDT"}))
    put(bot, "trend_cycle", cycle(-0.31))
    now = [time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))]
    sent = []
    prices = iter([0.1179, 0.1182, None, 0.1190])
    a = A.Alerts(tmp_path / "lab", clock=lambda: now[0], http=lambda u, p: (sent.append(p) or (200, {"code": 200})), price=lambda sym: next(prices))
    a.update({"device_key": DEVICE, "enabled": True, "signal": False, "report": True, "report_minutes": 10, "stale": False})
    a.tick(bot)  # first look: remembers the log
    a.tick(bot)
    assert len(sent) == 1 and sent[0]["title"] == "🕒 ENA 상태"
    assert "ENAUSDT 0.1179" in sent[0]["body"] and "목표 노출 -0.31 (숏)" in sent[0]["body"] and "다음 판단 약" in sent[0]["body"]
    now[0] += 9 * 60
    a.tick(bot)
    assert len(sent) == 1  # not yet 10 minutes
    now[0] += 61
    a.tick(bot)
    assert len(sent) == 2 and "(+0.25%)" in sent[1]["body"]  # change since the last report
    now[0] += 600
    a.tick(bot)
    assert len(sent) == 3 and "ENAUSDT" not in sent[2]["body"] and "목표 노출" in sent[2]["body"]  # no price available: the rest still goes out
    a.update({"report_minutes": 60})
    now[0] += 600
    a.tick(bot)
    assert len(sent) == 3  # the new, longer period is respected


def test_quiet_hours_silence_only_the_periodic_report(tmp_path):
    bot = make_bot(tmp_path / "b")
    put(bot, "trend_cycle", cycle(0.2))
    now = [time.mktime((2026, 11, 20, 23, 30, 0, 0, 0, -1))]
    sent = []
    a = A.Alerts(tmp_path / "lab", clock=lambda: now[0], http=lambda u, p: (sent.append(p) or (200, {"code": 200})), price=lambda s: 0.1)
    a.update({"device_key": DEVICE, "enabled": True, "report": True, "report_minutes": 1, "quiet": True, "quiet_from": "23:00", "quiet_to": "07:00", "stale": False})
    a.tick(bot)
    put(bot, "error", {"error": "boom"})
    put(bot, "trend_cycle", cycle(-0.5))  # a direction flip at night still goes out
    a.tick(bot)
    titles = [p["title"] for p in sent]
    assert "🕒 ENA 상태" not in titles and "⚠ ENA 봇 오류" in titles and any("방향 전환" in t for t in titles)
    now[0] = time.mktime((2026, 11, 21, 8, 0, 0, 0, 0, -1))
    a.tick(bot)
    assert any(p["title"] == "🕒 ENA 상태" for p in sent)  # morning: reports resume


def test_the_public_price_lookup_is_defensive():
    assert A.fetch_price("ENAUSDT", get=lambda u: {"result": {"list": [{"lastPrice": "0.1179"}]}}) == 0.1179
    assert A.fetch_price("ENAUSDT", get=lambda u: {}) is None
    assert A.fetch_price("bad symbol;", get=lambda u: (_ for _ in ()).throw(AssertionError("must not be called"))) is None

    def down(u):
        raise OSError("net")

    assert A.fetch_price("ENAUSDT", get=down) is None


def test_card_offers_the_period_and_quiet_hours_controls():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ("주기 상태 알림", "report_minutes", "조용한 시간", "quiet_from", "error_gap_min", "stale_hours", "signal_step"):
        assert needle in html, needle


def test_an_exchange_rate_limit_error_is_explained_in_korean():
    text = A._error_text(json.dumps({"error": "Bybit error 10006: Too many visits. Exceeded the API Rate Limit. (/v5/market/kline)"}))
    assert text.startswith("거래소 호출 한도 초과") and "일시적" in text and "10006" in text
    assert A._error_text(json.dumps({"error": "something else"})) == "something else"


# ---------------------------------------------------------------- entry price in the message, switchable (v1.12.6)
def put_kv(bot, key, value):
    con = sqlite3.connect(bot / "data" / "bot.db")
    con.execute("create table if not exists kv (key text primary key, value text)")
    con.execute("insert or replace into kv(key, value) values (?,?)", (key, json.dumps(value)))
    con.commit()
    con.close()


def test_the_open_position_is_found_in_the_saved_state_and_says_where(tmp_path):
    bot = make_bot(tmp_path / "b")
    assert A.read_position(bot / "data" / "bot.db") is None  # no kv table at all
    put_kv(bot, "risk_state", {"day": "2026-11-20", "peak_equity": 1000.0})  # numbers, but no position
    put_kv(bot, "trend_shadow_btc", {"qty": -2979.0, "entry": 0.10964})  # a shadow account: never the answer
    put_kv(bot, "trend_paper", {"equity": 1064.12, "pos": {"qty": -6082.0, "avg_entry": 0.11445}})
    pos = A.read_position(bot / "data" / "bot.db")
    assert pos["key"] == "trend_paper" and pos["entry"] == 0.11445 and pos["qty"] == -6082.0 and pos["side"] == "숏"
    assert pos["entry_field"] == "pos.avg_entry" and "trend_shadow_btc" in pos["others"] and not pos["flat"]
    put_kv(bot, "trend_paper", {"qty": 0.0, "entry": 0.11})
    assert A.read_position(bot / "data" / "bot.db")["flat"] is True


def test_the_message_follows_the_switches_and_signs_the_profit_by_side():
    base = dict(A.DEFAULTS)
    short = {"key": "k", "qty": -6082.0, "entry": 0.11445, "flat": False, "side": "숏"}
    raw = json.dumps({"price": 0.10402, "equity": 1064.12})
    title, body = A._signal_text(0.4, -0.58, raw, base, short)
    assert "신호 가격 0.10402" in body and "숏 평균 진입가 0.11445" in body and "진입가 대비 +9.11%" in body and "가상 계좌 1064.1 USDT" in body
    assert "수량" not in body  # off by default
    long = {"key": "k", "qty": 100.0, "entry": 0.10, "flat": False, "side": "롱"}
    assert "진입가 대비 +4.02%" in A._signal_text(-0.2, 0.3, raw, base, long)[1]
    assert "진입가 대비 -" in A._signal_text(-0.2, 0.3, json.dumps({"price": 0.09}), base, long)[1]
    off = dict(base, show_price=False, show_entry=False, show_pnl=False, show_equity=False, show_qty=True)
    body2 = A._signal_text(0.4, -0.58, raw, off, short)[1]
    assert "신호 가격" not in body2 and "평균 진입가" not in body2 and "진입가 대비" not in body2 and "가상 계좌" not in body2 and "수량 6,082" in body2
    none = A._signal_text(0.4, -0.58, raw, base, None)[1]  # no position found: the rest of the message still goes out
    assert "신호 가격 0.10402" in none and "평균 진입가" not in none
    assert "평균 진입가" not in A._signal_text(0.4, -0.58, raw, base, dict(short, flat=True))[1]


def test_entry_price_reaches_the_phone_and_the_status_report_and_the_settings_persist(tmp_path):
    bot = make_bot(tmp_path / "b")
    (bot / "config.json").write_text(json.dumps({"symbol": "ENAUSDT"}))
    put_kv(bot, "trend_paper", {"qty": -6082.0, "entry": 0.11445})
    put(bot, "trend_cycle", cycle(0.3))
    now = [time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))]
    sent = []
    a = A.Alerts(tmp_path / "lab", clock=lambda: now[0], http=lambda u, p: (sent.append(p) or (200, {"code": 200})), price=lambda s: 0.1040)
    a.update({"device_key": DEVICE, "enabled": True, "report": True, "report_minutes": 10, "stale": False})
    a.tick(bot)
    put(bot, "trend_cycle", cycle(-0.5, price=0.104))
    a.tick(bot)
    by = {p["title"]: p["body"] for p in sent}
    assert "숏 평균 진입가 0.11445" in by["🔄 ENA 추세 방향 전환"] and "신호 가격 0.104" in by["🔄 ENA 추세 방향 전환"]
    assert "숏 평균 진입가 0.11445" in by["🕒 ENA 상태"] and "진입가 대비 +9.13%" in by["🕒 ENA 상태"]
    pub = a.update({"show_entry": False, "show_qty": True})
    assert pub["show_entry"] is False and pub["show_qty"] is True and pub["show_price"] is True
    assert A.Alerts(tmp_path / "lab").public()["show_qty"] is True  # kept on disk


def test_the_card_offers_the_item_switches_and_names_the_source_of_the_position():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ("알림에 넣을 항목", "show_entry", "show_pnl", "show_qty", "8787 화면의 평단과 같은 값인지 확인하세요", "pos.entry_field"):
        assert needle in html, needle


# ---------------------------------------------------------------- reading the bot's own page (/api/state)
STATE = {"now": 1795156236505, "symbol": "ENAUSDT", "ticker": {"last": 0.10385, "pct24h": -6.9694},
         "cards": {"trend": {"detail": {"target_exposure": -0.58056}}},
         "accounts": {"base": {"qty": -6082.0, "entry": 0.11444824110789135, "exposure": -0.5898}, "shadow": {"qty": -2979.0, "entry": 0.1096368}, "ml_shadow": {"qty": -948.0, "entry": 0.1084612}}}


def test_the_monitor_state_gives_price_and_the_base_accounts_position(monkeypatch):
    seen = {}
    monkeypatch.undo()  # the autouse stub would hide the real function
    mon = A.read_monitor(8787, get=lambda url, timeout=2.5: seen.update(url=url) or STATE)
    assert seen["url"] == "http://127.0.0.1:8787/api/state"
    assert mon["price"] == 0.10385 and mon["pct24h"] == -6.9694 and mon["base"] == {"qty": -6082.0, "entry": 0.11444824110789135, "liq": None} and mon["exposure"] == -0.58056  # the bot reports no liquidation price
    pos = A.position_from_monitor(mon)
    assert pos["entry"] == 0.11444824110789135 and pos["side"] == "숏" and pos["source"] == "monitor" and pos["entry_field"] == "accounts.base.entry" and not pos["flat"]
    assert A.position_from_monitor({"base": {"qty": 0.0, "entry": 0.0}})["flat"] is True
    for odd in ({}, {"accounts": []}, {"accounts": {"base": {"qty": "x"}}}, []):
        got = A.read_monitor(get=lambda u, t=2.5, v=odd: v)
        assert got is None or got["base"] is None
    assert A.position_from_monitor(None) is None

    def down(u, t=2.5):
        raise OSError("refused")

    assert A.read_monitor(get=down) is None


def test_alerts_use_the_pages_own_entry_price_and_price_for_the_profit(tmp_path):
    bot = make_bot(tmp_path / "b")
    (bot / "config.json").write_text(json.dumps({"symbol": "ENAUSDT"}))
    put(bot, "trend_cycle", cycle(0.3))
    now = [time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))]
    sent = []
    mon = {"symbol": "ENAUSDT", "price": 0.10385, "pct24h": -6.97, "base": {"qty": -6082.0, "entry": 0.11444824110789135}, "exposure": -0.58}
    a = A.Alerts(tmp_path / "lab", clock=lambda: now[0], http=lambda u, p: (sent.append(p) or (200, {"code": 200})), price=lambda s: 9.9, monitor=lambda: mon)
    a.update({"device_key": DEVICE, "enabled": True, "report": True, "report_minutes": 10, "stale": False})
    a.tick(bot)
    put(bot, "trend_cycle", cycle(-0.58, price=0.1040))
    a.tick(bot)
    by = {p["title"]: p["body"] for p in sent}
    sig = by["🔄 ENA 추세 방향 전환"]
    assert "숏 평균 진입가 0.114448" in sig and "신호 가격 0.104" in sig and "진입가 대비 +9.26%" in sig  # profit from the page's price 0.10385, not the 0.1040 of the event
    rep = by["🕒 ENA 상태"]
    assert "ENAUSDT 0.10385" in rep and "9.9" not in rep.replace("0.10385", "")  # the outside ticker (9.9) was not even asked
    assert a.position(bot)["source"] == "monitor"
    mon.clear()  # the page is down: the saved state is the fallback
    put_kv(bot, "trend_paper", {"qty": -6082.0, "entry": 0.11445})
    mon_down = A.Alerts(tmp_path / "lab2", monitor=lambda: None)
    assert mon_down.position(bot)["source"] == "kv"


# ---------------------------------------------------------------- the short message: direction, entry, now, profit %, liquidation price
SHORT = {"side": "숏", "qty": -6082.0, "entry": 0.113133, "liq": 0.2201, "flat": False}


def test_the_short_message_has_only_direction_entry_now_profit_and_liquidation():
    c = dict(A.DEFAULTS, style="compact")
    title, body = A._compact(c, SHORT, 0.09924, -0.54)
    assert title == "🟢 ENA 숏 +12.3%" and body == "진입 0.113133 → 현재 0.09924\n수익률 +12.28% · 청산가 0.2201"
    assert "목표 노출" not in body and "다음 판단" not in body and "마지막 판단" not in body
    t2, b2 = A._compact(c, dict(SHORT, side="롱", qty=100.0, liq=0.05), 0.10, None)  # a long loses when the price falls below the entry
    assert t2 == "🔴 ENA 롱 -11.6%" and "수익률 -11.61%" in b2 and "청산가 0.05" in b2


def test_the_short_message_follows_the_switches_and_skips_a_liquidation_price_the_bot_does_not_report():
    c = dict(A.DEFAULTS, style="compact", show_liq=False, show_entry=False)
    title, body = A._compact(c, SHORT, 0.09924, None)
    assert "청산가" not in body and "진입" not in body and body == "현재 0.09924\n수익률 +12.28%"
    title, body = A._compact(dict(A.DEFAULTS, style="compact"), dict(SHORT, liq=None), 0.09924, None)
    assert "청산가" not in body and "수익률 +12.28%" in body
    title, body = A._compact(dict(A.DEFAULTS, style="compact", show_pnl=False, show_qty=True), SHORT, 0.09924, None)
    assert title == "🟢 ENA 숏" and "수량 6,082" in body and "수익률" not in body


def test_flat_has_a_one_line_message():
    c = dict(A.DEFAULTS, style="compact")
    assert A._compact(c, None, 0.0992, -0.54) == ("⚪ ENA 포지션 없음", "현재 0.0992 · 목표 방향 숏")
    assert A._compact(c, dict(SHORT, flat=True), None, None)[1] == "보유 포지션이 없습니다"


def test_a_direction_flip_message_is_short_too():
    c = dict(A.DEFAULTS, style="compact")
    title, body = A._signal_text(0.5, -0.54, '{"price": 0.1, "equity": 100}', c, SHORT, 0.09924)
    assert title == "🔄 ENA 방향 전환: 롱 → 숏" and body == "진입 0.113133 → 현재 0.09924\n수익률 +12.28% · 청산가 0.2201" and "가상 계좌" not in body
    title, body = A._signal_text(-0.54, -0.64, '{"price": 0.1}', c, None, 0.0992)
    assert title == "📈 ENA 신호: 숏 (노출 -0.64)" and body == "현재 0.0992 · 신호가 0.1"


def test_the_periodic_report_is_the_short_message_and_the_settings_persist(tmp_path):
    bot = make_bot(tmp_path / "b")
    (bot / "config.json").write_text(json.dumps({"symbol": "ENAUSDT"}))
    put(bot, "trend_cycle", cycle(-0.54))
    now = [time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))]
    sent = []
    mon = {"symbol": "ENAUSDT", "price": 0.09924, "pct24h": 0.68, "base": {"qty": -6082.0, "entry": 0.113133, "liq": 0.2201}, "exposure": -0.54}
    a = A.Alerts(tmp_path / "lab", clock=lambda: now[0], http=lambda u, p: (sent.append(p) or (200, {"code": 200})), price=lambda s: 9.9, monitor=lambda: mon)
    a.update({"device_key": DEVICE, "enabled": True, "report": True, "report_minutes": 10, "stale": False, "signal": False, "style": "compact"})
    assert a.config()["style"] == "compact"
    a.tick(bot)
    now[0] += 11 * 60
    a.tick(bot)
    assert sent and sent[-1]["title"] == "🟢 ENA 숏 +12.3%" and sent[-1]["body"] == "진입 0.113133 → 현재 0.09924\n수익률 +12.28% · 청산가 0.2201"
    a.update({"style": "detail", "show_liq": False})
    assert a.config()["style"] == "detail" and a.config()["show_liq"] is False
    a.update({"style": "nonsense"})
    assert a.config()["style"] == "detail"  # an unknown value changes nothing


def test_the_monitor_reader_finds_a_liquidation_price_by_name():
    assert A._liq({"qty": -1, "entry": 1, "liq_price": 0.22}) == 0.22 and A._liq({"liquidationPrice": 3.5}) == 3.5
    assert A._liq({"qty": -1, "entry": 1}) is None and A._liq({"liq": True}) is None and A._liq({"liq": 0}) is None


def test_a_liquidation_price_in_the_pages_state_reaches_the_position(monkeypatch):
    monkeypatch.undo()
    state = {"symbol": "ENAUSDT", "ticker": {"last": 0.1}, "accounts": {"base": {"qty": -6082, "entry": 0.113, "liquidation_price": 0.22}}}
    mon = A.read_monitor(8787, get=lambda url, timeout=2.5: state)
    assert mon["base"]["liq"] == 0.22 and A.position_from_monitor(mon)["liq"] == 0.22


def test_the_settings_page_gets_the_style_and_the_liquidation_switch(tmp_path):
    a = A.Alerts(tmp_path / "lab")
    pub = a.public()
    assert pub["style"] in ("compact", "detail") and pub["show_liq"] is True
    a.update({"style": "compact", "show_liq": False})
    assert a.public()["style"] == "compact" and a.public()["show_liq"] is False
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    assert "메시지 형식:" in html and "show_liq: shLq.checked, style: stl.value" in html
