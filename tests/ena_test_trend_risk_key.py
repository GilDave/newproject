"""ENA 저장소 tests/ 로 복사해 쓰는 테스트(이 저장소의 테스트 러너에서는 실행되지 않는다)."""
import json

from test_trend_engine import _engine

from ena_bot.config import Config
from ena_bot.notify import Notifier
from ena_bot.store import Store
from ena_bot.trend_engine import PaperTrendBroker, TREND_RISK_KEY, TrendEngine


def test_old_engine_halt_does_not_stop_trend_bot(tmp_path):
    cfg = Config(); cfg.data_dir = str(tmp_path)
    store = Store(":memory:")
    store.kv_set("risk_state", {"peak_equity": 1073.0, "halted": True, "halt_reason": "max_drawdown 10.3% >= 10.0%"})
    eng = TrendEngine(cfg, None, PaperTrendBroker(cfg), store, Notifier())
    assert eng.risk.state.halted is False


def test_trend_writes_own_key_and_leaves_old_key(tmp_path):
    eng, _, _ = _engine(tmp_path, "up")
    eng.store.kv_set("risk_state", {"halted": True, "halt_reason": "old"})
    assert eng.run_cycle() is True
    assert eng.store.kv_get("risk_state") == {"halted": True, "halt_reason": "old"}  # 건드리지 않음
    assert eng.store.kv_get(TREND_RISK_KEY)["peak_equity"] > 0


def test_trend_state_survives_restart(tmp_path):
    cfg = Config(); cfg.data_dir = str(tmp_path)
    store = Store(":memory:")
    store.kv_set(TREND_RISK_KEY, {"halted": True, "halt_reason": "trend dd"})
    eng = TrendEngine(cfg, None, PaperTrendBroker(cfg), store, Notifier())
    assert eng.risk.state.halted is True and eng.risk.state.halt_reason == "trend dd"


def test_dashboard_risk_card_prefers_trend_key(tmp_path):
    from ena_bot.scores import card_risk
    cfg = Config(); cfg.data_dir = str(tmp_path)
    st = Store(":memory:")
    st.kv_set("risk_state", {"peak_equity": 1000.0, "halted": True, "halt_reason": "old"})
    acc = {"base": {"equity": 1000.0}}
    assert card_risk(cfg, st, acc)["detail"]["halted"] is True          # 추세 키 없음 → 예전 키
    st.kv_set(TREND_RISK_KEY, {"peak_equity": 1000.0, "halted": False})
    assert card_risk(cfg, st, acc)["detail"]["halted"] is False
