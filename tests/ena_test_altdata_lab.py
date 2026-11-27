"""ENA 저장소에서 실행하는 테스트: ena_altdata_lab.py · ena_idea_lab.py · ena_macro_study.py 를 ENA 루트에 두고 tests/ 로 복사해 쓴다."""
import json

import numpy as np

import ena_altdata_lab as al

DAY = 86_400_000


def test_parsers_handle_the_documented_shapes():
    fng = json.dumps({"data": [{"value": "80", "timestamp": "1700086400"}, {"value": "20", "timestamp": "1700000000"}]})
    d, v = al.parse_fng(fng)
    assert list(v) == [20.0, 80.0] and d[0] == 1_700_000_000_000          # 시간순으로 정렬된다
    chart = json.dumps([{"date": "1700000000", "totalCirculatingUSD": {"peggedUSD": 5.0}}, {"date": "1700086400", "totalCirculating": {"peggedUSD": 6.0}}, {"date": "1700172800"}])
    assert list(al.parse_llama_chart(chart)[1]) == [5.0, 6.0]            # 값이 없는 행은 건너뛴다
    tvl = json.dumps({"tvl": [{"date": 1700000000, "totalLiquidityUSD": 1.5}]})
    assert list(al.parse_protocol_tvl(tvl)[1]) == [1.5]
    lst = json.dumps({"peggedAssets": [{"id": "1", "symbol": "USDT"}, {"id": "146", "symbol": "USDe"}]})
    assert al.find_usde_id(lst) == "146"


def test_daily_value_is_only_known_from_the_next_day():
    d = np.array([10 * DAY, 11 * DAY], dtype=np.int64)
    flag = np.array([1.0, 0.0])
    out = al.asof_known(d, flag, np.array([10 * DAY + 3_600_000, 11 * DAY - 1, 11 * DAY, 12 * DAY, 12 * DAY + 5]))
    assert np.isnan(out[0])                      # 10일 값은 11일 00:00 에야 알려진다
    assert np.isnan(out[1]) and out[2] == 1.0    # 11일 00:00 부터 10일 값
    assert out[3] == 0.0 and out[4] == 0.0       # 12일 00:00 부터 11일 값


def test_change_30d_and_multipliers_only_cut_the_intended_side():
    days = np.arange(0, 80, dtype=np.int64) * DAY
    fng = np.where(np.arange(80) >= 40, 10.0, 50.0)                      # 40일째부터 극단적 공포
    ts = days + 3_600_000
    ts = ts[45:50]
    ml, ms_ = al.multipliers("A1", {"fng": (days, fng)}, ts)
    assert (ml == 1.0).all() and (ms_ == 0.5).all()                      # A1 은 숏만 줄인다
    ml, ms_ = al.multipliers("A2", {"fng": (days, np.full(80, 90.0))}, ts)
    assert (ml == 0.5).all() and (ms_ == 1.0).all()                      # A2 는 롱만 줄인다
    vals = np.r_[np.full(40, 100.0), np.linspace(100, 60, 40)]
    ch = al.change_30d(days, vals)
    assert np.isnan(ch[0]) and ch[79] < 0
    ml, ms_ = al.multipliers("A3", {"stable_total": (days, vals)}, days[60:65] + 1000)
    assert (ml == 0.5).all() and (ms_ == 0.5).all()                      # 유동성 수축이면 전체


def test_each_source_may_fail_independently():
    def fetch(url):
        if "alternative.me" in url:
            return json.dumps({"data": [{"value": "50", "timestamp": "1700000000"}]})
        raise OSError("blocked")
    series, errors = al.fetch_all(None, use_cache=False, fetch=fetch)
    assert "fng" in series and {"stable_total", "usde", "ethena_tvl"} <= set(errors)
