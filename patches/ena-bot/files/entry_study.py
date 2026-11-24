"""진입 계획 규칙(entry_plan)이 과거에 실제로 나았는지 모의 매매로 확인한다. 읽기 전용이며 매매·모델에 영향이 없다.

실행: python -m ena_bot.entry_study [--config config.json] [--symbols ...]
규칙은 그대로 두고 18개 코인의 1시간 이력을 1시간·4시간·1일봉으로 묶어 같은 계획을 매 봉 계산한 뒤, 계획대로 진입·손절·목표를 모의 체결한다.
- 진입: 계획의 진입가에 지정가. 다음 wait_bars봉 안에 닿으면 체결(갭이면 시가). 닿지 않으면 미체결.
- 청산: 손절 또는 첫 목표 중 먼저 닿는 쪽. 같은 봉에서 둘 다 닿으면 손절을 먼저 맞은 것으로 본다(보수적). hold_bars봉 안에 안 닿으면 종가 청산.
- 비용: 양쪽 체결에 수수료 0.055%와 슬리피지 0.01%.
- 결과는 위험(R) 단위의 순손익. 같은 날 여러 코인의 거래는 한 묶음으로 묶어 신뢰구간을 계산하고, 그룹 수만큼 유의수준을 나눈다(다중 비교 보정).
기준(서로 다른 날 30개·코인 5개 이상이고 보정한 신뢰구간 하한이 0 초과)을 넘지 못하면 '기준 미달'이다.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from . import entry_plan as ep
from . import shock
from .shock_study import coin_arrays, resample_ohlcv

FEE, SLIP = 0.00055, 0.0001
WAIT_BARS, HOLD_BARS, MIN_SCORE = 6, 48, 2
FACTORS = {"1h": 1, "4h": 4, "1d": 24}
DAY_MS = 24 * 3600_000


def fill_and_exit(p: dict, o: list, h: list, l: list, c: list, t: int, wait: int = WAIT_BARS, hold: int = HOLD_BARS, fee: float = FEE, slip: float = SLIP) -> dict | None:
    """봉 t에서 만든 계획 p를 다음 봉들에서 모의 체결한다. 체결되지 않으면 None, 체결되면 {R, outcome, entry_i, exit_i}."""
    n = len(c)
    d = 1 if p["direction"] == "long" else -1
    entry, stop, t1 = p["entry"], p["stop"], p["targets"][0]
    risk = abs(entry - stop)
    fill = None
    for j in range(t + 1, min(n, t + 1 + wait)):
        touched = l[j] <= entry if d == 1 else h[j] >= entry
        if touched:
            gap = (d == 1 and o[j] < entry) or (d == -1 and o[j] > entry)
            fill = (j, o[j] if gap else entry)
            break
    if fill is None:
        return None
    j, px = fill
    stop_hit_same_bar = (l[j] <= stop) if d == 1 else (h[j] >= stop)
    exit_i, exit_px, outcome = None, None, None
    if stop_hit_same_bar:
        exit_i, exit_px, outcome = j, stop, "loss"                  # 체결된 봉에서 손절 가격까지 닿았다: 손절 먼저(보수적)
    else:
        for k in range(j + 1, min(n, j + 1 + hold)):
            s_hit = (l[k] <= stop) if d == 1 else (h[k] >= stop)
            t_hit = (h[k] >= t1) if d == 1 else (l[k] <= t1)
            if s_hit:                                                # 같은 봉에서 둘 다 닿으면 손절 우선
                gap = (d == 1 and o[k] < stop) or (d == -1 and o[k] > stop)
                exit_i, exit_px, outcome = k, (o[k] if gap else stop), "loss"
                break
            if t_hit:
                exit_i, exit_px, outcome = k, t1, "win"
                break
        if exit_i is None:
            exit_i = min(n - 1, j + hold)
            exit_px, outcome = c[exit_i], "timeout"
    risk_px = abs(px - stop) if abs(px - stop) > 0 else risk
    gross = d * (exit_px - px) / risk_px
    cost = (px + exit_px) * (fee + slip) / risk_px
    return {"R": gross - cost, "outcome": outcome, "entry_i": j, "exit_i": exit_i}


def simulate(o: list, h: list, l: list, c: list, ts: list, **kw) -> dict:
    """한 코인·한 시간봉의 모든 봉을 훑으며 계획이 생길 때마다 모의 거래. 거래가 열려 있는 동안의 신호는 건너뛴다."""
    ind = ep.indicators(o, h, l, c)
    trades, signals = [], 0
    busy_until = -1
    for t in range(ep.MIN_BARS, len(c) - 1):
        if t <= busy_until:
            continue
        cands = [x for x in ep.candidates(ind, t) if x["state"] in ("in_zone", "approaching") and x["score"] >= MIN_SCORE]
        if not cands:
            continue
        p = ep.best_for(cands)
        signals += 1
        tr = fill_and_exit(p, o, h, l, c, t, **kw)
        if tr is None:
            busy_until = t + kw.get("wait", WAIT_BARS)
            continue
        busy_until = tr["exit_i"]
        trades.append({**tr, "setup": p["setup"], "direction": p["direction"], "aligned": p["aligned_trend"], "ts": int(ts[tr["entry_i"]])})
    return {"trades": trades, "signals": signals}


def study(pool: dict, min_bars: int = 400, groups_alpha: int | None = None) -> dict:
    """pool: {심볼: 코인 객체}. 시간봉(1h·4h·1d) × 계획 종류별 통계와 판정."""
    data: dict = {}
    for tf, factor in FACTORS.items():
        for sym, coin in pool.items():
            ts, o, h, l, c, v = resample_ohlcv(*coin_arrays(coin), factor)
            if len(c) < min_bars:                          # 이력이 짧은 코인·시간봉은 건너뛴다
                continue
            res = simulate(list(map(float, o)), list(map(float, h)), list(map(float, l)), list(map(float, c)), list(ts))
            for tr in res["trades"]:
                data.setdefault((tf, tr["setup"]), []).append({**tr, "symbol": sym})
            data.setdefault((tf, "_signals"), []).append(res["signals"])
    keys = [(tf, s) for tf in FACTORS for s in ("pullback", "reversion")]
    alpha = 0.05 / (groups_alpha or len(keys))
    groups = {}
    for tf, setup in keys:
        tr = data.get((tf, setup), [])
        vals = [t["R"] for t in tr]
        clusters = [t["ts"] // DAY_MS for t in tr]
        s = shock._summarize(vals, len({t["symbol"] for t in tr}), clusters, alpha)
        wins = sum(1 for t in tr if t["outcome"] == "win")
        losses = sum(1 for t in tr if t["outcome"] == "loss")
        s.update({"win_rate": (wins / len(tr)) if tr else None, "loss_rate": (losses / len(tr)) if tr else None, "signals": int(sum(data.get((tf, "_signals"), [])))})
        if s["n"] == 0:
            s["verdict"] = {"use": False, "why": "거래가 없습니다"}
        elif s["n_clusters"] < shock.MIN_EVENTS or s["n_coins"] < shock.MIN_COINS:
            s["verdict"] = {"use": False, "why": f"표본 부족(서로 다른 날 {s['n_clusters']}개·코인 {s['n_coins']}개, 필요 {shock.MIN_EVENTS}개·{shock.MIN_COINS}개)"}
        elif s["ci"][0] > 0:
            s["verdict"] = {"use": True, "why": f"평균 {s['mean']:+.2f}R, 보정한 신뢰구간 하한 {s['ci'][0]:+.2f}R > 0"}
        else:
            s["verdict"] = {"use": False, "why": f"기준 미달(평균 {s['mean']:+.2f}R, 보정한 신뢰구간 {s['ci'][0]:+.2f}~{s['ci'][1]:+.2f}R이 0을 포함하거나 음수)"}
        groups[f"{tf}|{setup}"] = s
    return {"groups": groups, "alpha": alpha, "params": {"fee": FEE, "slippage": SLIP, "wait_bars": WAIT_BARS, "hold_bars": HOLD_BARS, "min_score": MIN_SCORE}, "coins": sorted(pool),
            "generated": int(time.time())}


NAMES = {"pullback": "추세 눌림목", "reversion": "밴드 반전"}


def render(res: dict) -> str:
    lines = [f"진입 계획 규칙의 과거 검증 — 코인 {len(res['coins'])}개 · 수수료 {FEE:.3%}+슬리피지 {SLIP:.2%} 양쪽 · 손절·첫 목표(손절 우선)", ""]
    lines.append(f"시간봉 | 계획 | 신호 | 거래 | 서로 다른 날 | 승률 | 평균(R) | {(1 - res['alpha']) * 100:.2f}% 신뢰구간(R, 날 단위·다중 비교 보정) | 판정")
    for key, s in res["groups"].items():
        tf, setup = key.split("|")
        if not s["n"]:
            lines.append(f"{tf} | {NAMES[setup]} | {s['signals']} | 0 | 0 | - | - | - | {s['verdict']['why']}")
            continue
        lines.append(f"{tf} | {NAMES[setup]} | {s['signals']} | {s['n']} | {s['n_clusters']} | {s['win_rate']:.0%} | {s['mean']:+.3f} | {s['ci'][0]:+.2f} ~ {s['ci'][1]:+.2f} | {s['verdict']['why']}")
    lines += ["", "(R = 손절까지의 거리 1배. 평균이 0보다 작으면 비용을 뺀 뒤 손실. 15분봉은 이력이 없어 검증하지 않았습니다. 상장 이력이 긴 코인만 있어 생존 편향이 있을 수 있습니다.)"]
    return "\n".join(lines)


def load_saved(data_path) -> dict:
    """대시보드용 요약. 없거나 깨졌으면 {"ok": False}."""
    try:
        d = json.loads((Path(data_path) / "entry_study.json").read_text(encoding="utf-8"))
        groups = {k: {"n": g.get("n"), "n_clusters": g.get("n_clusters"), "mean": g.get("mean"), "win_rate": g.get("win_rate"), "ci": g.get("ci"), "verdict": g.get("verdict")}
                  for k, g in (d.get("groups") or {}).items()}
        age = (time.time() - float(d.get("generated", 0))) / 86400
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ok": False}
    return {"ok": True, "groups": groups, "coins": len(d.get("coins") or []), "age_days": round(age, 1)}


def main(argv: list[str] | None = None) -> int:
    from .config import load_config
    from .learn_data import DEFAULT_SYMBOLS, load_coin

    ap = argparse.ArgumentParser(description="진입 계획 규칙의 과거 검증(읽기 전용)")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--symbols", default="")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    cache = cfg.data_path / "learn_cache"
    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()] or DEFAULT_SYMBOLS
    pool = {s: c for s in symbols if (c := load_coin(s, cache)) is not None}
    if not pool:
        print(f"학습 캐시가 없습니다: {cache}  (먼저 learn-train 으로 데이터를 받으세요)")
        return 2
    print(f"{len(pool)}개 코인으로 모의 매매 중… (몇 분 걸릴 수 있습니다)")
    res = study(pool)
    print(render(res))
    out = cfg.data_path / "entry_study.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    print(f"\n저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
