"""ENA 저장소에서 실행하는 테스트: ena_tvl_lab.py 와 같은 폴더의 ena_altdata_lab.py · ena_idea_lab.py · ena_macro_study.py 가 ENA 루트에 있어야 한다."""
import json

import numpy as np

import ena_tvl_lab as tl

DAY = 86_400_000


def test_parse_chain_tvl_and_failures_are_independent():
    d, v = tl.parse_chain_tvl(json.dumps([{"date": 1700086400, "tvl": 2.0}, {"date": 1700000000, "tvl": 1.0}, {"date": 1700172800}]))
    assert list(v) == [1.0, 2.0] and d[0] == 1_700_000_000_000

    def fetch(url):
        if "Solana" in url:
            return json.dumps([{"date": 1700000000, "tvl": 5.0}])
        if "protocol/ethena" in url and "usde" not in url:
            return json.dumps({"tvl": [{"date": 1700000000, "totalLiquidityUSD": 3.0}]})
        raise OSError("blocked")
    series, errors = tl.fetch_tvl(None, use_cache=False, fetch=fetch)
    assert set(series) == {"SOLUSDT", "ENAUSDT"} and "ETHUSDT" in errors and "ENAUSDT" not in errors


def test_multiplier_halves_only_after_a_decline_is_known():
    days = np.arange(0, 100, dtype=np.int64) * DAY
    vals = np.r_[np.full(50, 100.0), np.linspace(100, 70, 50)]      # 50일째부터 감소
    ts = days[40:99] + 3_600_000
    m = tl.multiplier((days, vals), ts)
    assert m[0] == 1.0 and m[-1] == 0.5
    on = np.where(m < 1)[0]
    first_ts = ts[on[0]]
    assert first_ts >= 51 * DAY      # 감소가 시작된 첫날(50일) 값은 다음 날 00:00 이후에야 쓸 수 있다(미래 누수 없음)
    assert tl.multiplier((days, vals), ts, 60)[0] == 1.0
