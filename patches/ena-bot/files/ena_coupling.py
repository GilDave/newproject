"""ENA 와 미국 주식(나스닥 종합·S&P500)이 '같이 움직이는' 정도를 잰다(읽기 전용). 예측이 아니라 동행(같은 날 방향이 같은가)을 본다.
미국 장 마감(16:00 ET)에서 다음 마감까지의 ENA·BTC 수익률을 그날의 나스닥 일간 수익률과 짝지어 상관·베타·최근 구간 변화를 계산한다.
QQQ 가격은 받지 않고, 같은 나스닥 지수군인 FRED NASDAQCOM(나스닥 종합)을 쓴다(일간 방향은 QQQ 와 거의 같다).
실행(ENA 폴더에서):  .venv/bin/python ena_coupling.py        (먼저 ena_macro_study.py 를 돌려 data/macro_cache 가 있으면 빠르다)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_macro_study as m  # noqa: E402

WINDOWS = (30, 60, 90)


def pairs(index_rows, hourly):
    """[(날짜, 지수 수익률, 코인 수익률)]: 연속한 두 거래일 사이(마감→마감)."""
    out = []
    for (d0, v0), (d1, v1) in zip(index_rows, index_rows[1:]):
        if v0 <= 0:
            continue
        p0, p1 = m.price_at(hourly, m.us_close_ms(d0)), m.price_at(hourly, m.us_close_ms(d1))
        if p0 and p1:
            out.append((d1, v1 / v0 - 1, p1 / p0 - 1))
    return out


def corr(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 10 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def boot_corr(a, b, iters=2000, seed=0):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 30:
        return None
    rng = np.random.default_rng(seed)
    r = []
    for _ in range(iters):
        i = rng.integers(0, len(a), len(a))
        c = corr(a[i], b[i])
        if c is not None:
            r.append(c)
    return (float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))) if r else None


def beta(x, y):
    """y ≈ a + b·x 의 b."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.cov(x, y)[0, 1] / x.var(ddof=1)) if len(x) > 10 and x.var() > 0 else None


def partial(nq, ena, btc):
    """BTC 의 영향을 뺀 뒤 ENA 와 나스닥의 상관: ENA·나스닥을 각각 BTC 로 회귀한 잔차끼리의 상관."""
    nq, ena, btc = (np.asarray(v, float) for v in (nq, ena, btc))
    if len(nq) < 30 or btc.var() == 0:
        return None
    res = lambda y: y - (np.polyfit(btc, y, 1)[0] * btc + np.polyfit(btc, y, 1)[1])
    return corr(res(nq), res(ena))


def study(idx_rows, ena_h, btc_h):
    pe = pairs(idx_rows, ena_h)
    pb = {d: r for d, _, r in pairs(idx_rows, btc_h)}
    rows = [(d, q, e, pb[d]) for d, q, e in pe if d in pb]
    if len(rows) < 60:
        return {"n": len(rows)}
    dates = [r[0] for r in rows]
    nq, ena, btc = (np.array([r[i] for r in rows]) for i in (1, 2, 3))
    out = {"n": len(rows), "from": dates[0], "to": dates[-1], "ena_nq": corr(nq, ena), "ena_nq_ci": boot_corr(nq, ena), "btc_nq": corr(nq, btc), "ena_btc": corr(btc, ena),
           "beta_ena": beta(nq, ena), "partial": partial(nq, ena, btc), "windows": {}}
    for w in WINDOWS:
        if len(rows) >= 2 * w:
            out["windows"][w] = {"recent": corr(nq[-w:], ena[-w:]), "before": corr(nq[-2 * w:-w], ena[-2 * w:-w]), "btc_recent": corr(nq[-w:], btc[-w:])}
    roll = [corr(nq[i - 60:i], ena[i - 60:i]) for i in range(60, len(rows) + 1)]
    roll = [r for r in roll if r is not None]
    if roll:
        out["roll60"] = {"now": roll[-1], "p10": float(np.percentile(roll, 10)), "median": float(np.median(roll)), "p90": float(np.percentile(roll, 90)), "share_gt_0_5": float(np.mean([r > 0.5 for r in roll]))}
    down, up = nq < 0, nq > 0
    out["updown"] = {"nq_up_n": int(up.sum()), "ena_on_up": float(ena[up].mean() * 100), "nq_down_n": int(down.sum()), "ena_on_down": float(ena[down].mean() * 100),
                     "nq_up_mean": float(nq[up].mean() * 100), "nq_down_mean": float(nq[down].mean() * 100)}
    out["same_sign"] = float(np.mean(np.sign(nq) == np.sign(ena)) * 100)
    out["r2_ena"] = (out["ena_nq"] ** 2) * 100 if out["ena_nq"] is not None else None
    return out


def render(res: dict, label: str) -> str:
    if res.get("n", 0) < 60:
        return f"[{label}] 짝지을 수 있는 거래일이 {res.get('n', 0)}일뿐이라 계산하지 않았습니다(60일 필요)."
    f = lambda v, d=2: "—" if v is None else f"{v:+.{d}f}"
    ci = res["ena_nq_ci"]
    L = [f"## ENA ↔ {label} (미국 장 마감~마감 일간 수익률, {res['from']} ~ {res['to']}, {res['n']}일)", "",
         f"- ENA–{label} 상관 {f(res['ena_nq'])}" + (f" (95% 구간 {f(ci[0])} ~ {f(ci[1])})" if ci else "") + f" · 설명되는 변동 {res['r2_ena']:.0f}% · 베타 {f(res['beta_ena'])} (지수가 1% 움직일 때 ENA 평균 {f(res['beta_ena'])}%)",
         f"- 비교: BTC–{label} {f(res['btc_nq'])} · ENA–BTC {f(res['ena_btc'])} · BTC 영향을 뺀 ENA–{label} 상관 {f(res['partial'])}",
         f"- 같은 날 방향이 같았던 비율 {res['same_sign']:.0f}% (동전 던지기 50%)"]
    if "roll60" in res:
        r = res["roll60"]
        L.append(f"- 60일 이동 상관: 지금 {f(r['now'])} · 전체 기간 중앙값 {f(r['median'])} (하위 10% {f(r['p10'])} ~ 상위 10% {f(r['p90'])}) · 0.5 넘은 구간 {r['share_gt_0_5'] * 100:.0f}%")
    for w, v in res["windows"].items():
        L.append(f"- 최근 {w}거래일 상관 {f(v['recent'])} (그 직전 {w}일 {f(v['before'])}) · 같은 구간 BTC {f(v['btc_recent'])}")
    u = res["updown"]
    L.append(f"- 지수 상승일 {u['nq_up_n']}일(평균 {f(u['nq_up_mean'])}%)에 ENA 평균 {f(u['ena_on_up'])}% · 하락일 {u['nq_down_n']}일(평균 {f(u['nq_down_mean'])}%)에 ENA 평균 {f(u['ena_on_down'])}%")
    L += ["", "읽는 법:", "- 상관 0.5 이상이면 '꽤 같이 움직임', 0.7 이상이면 강한 동행, 0.3 미만이면 약함입니다. 동행은 '나스닥을 보면 ENA 를 예측한다'와 다릅니다(같은 날 움직임을 같이 설명할 뿐, 다음 움직임을 알려 주지 않습니다).",
          "- 코인 전체가 위험자산이라 BTC 와 함께 움직이는 부분이 큽니다. 'BTC 영향을 뺀 상관'이 ENA 만의 연결입니다.",
          "- 일간 수익률 기준이라 장중·밤사이 급변(예: 오늘 밤 급락)의 원인을 직접 보여 주지는 않습니다. 짧은 구간 상관은 변동이 큽니다."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="data/ENAUSDT_60m.csv")
    ap.add_argument("--btc", default="data/learn_cache/BTCUSDT_k60.npy")
    ap.add_argument("--cache", default="data/macro_cache")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args(argv)
    if not Path(a.csv).is_file():
        print(f"ENA 시세 파일이 없습니다: {a.csv}")
        return 1
    ena = m.load_ena_hourly(Path(a.csv))
    btc_arr = np.load(a.btc) if Path(a.btc).is_file() else None
    btc = {int(r[0]) // m.HOUR * m.HOUR: float(r[4]) for r in btc_arr} if btc_arr is not None else {}
    if not btc:
        print("BTC 시세 파일을 읽지 못했습니다:", a.btc)
        return 1
    for sid, label in (("NASDAQCOM", "나스닥 종합"), ("SP500", "S&P 500")):
        try:
            rows = m.fetch_fred(sid, Path(a.cache), use_cache=not a.no_cache)
        except Exception as exc:  # noqa: BLE001
            print(f"[{label}] 수집 실패: {str(exc)[:80]}")
            continue
        print(render(study(rows, ena, btc), label), "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
