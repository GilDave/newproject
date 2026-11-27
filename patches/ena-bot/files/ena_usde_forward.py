"""ENA 고유 신호(USDe 유통량·Ethena TVL 30일 감소 → ENA ×0.5)의 전진검증 도구(읽기 전용, 매매·설정은 건드리지 않는다).
규칙·판정은 ena_round4_prereg.md 의 '전진검증 규칙'에 고정했다. 과거를 다시 쓰지 않도록 매일의 값과 신호를 기록해 두고, 기록 시작일 이후만 재생한다.

  .venv/bin/python ena_usde_forward.py --log     오늘까지의 새 날짜를 기록 (여러 번 실행해도 안전, 매일 또는 주 1회)
  .venv/bin/python ena_usde_forward.py --eval    기록 시작일 이후 ENA 기준 전략 vs 신호 적용 전략 비교
  .venv/bin/python ena_usde_forward.py --daily   기록 + ENA 시세 갱신 + 평가를 한 번에 하고 data/forward/usde_eval.txt 에 저장 (launchd 가 매일 실행)
같은 폴더에 ena_altdata_lab.py · ena_idea_lab.py · ena_macro_study.py 가 있어야 한다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_altdata_lab as al  # noqa: E402
import ena_idea_lab as lab  # noqa: E402

LOG = Path("data/forward/usde_log.csv")
EVAL_OUT = Path("data/forward/usde_eval.txt")
FIELDS = ["date", "usde_supply", "usde_30d", "usde_flag", "tvl", "tvl_30d", "tvl_flag"]
DAY = al.DAY
MID_DAYS, FINAL_DAYS = 90, 180


def day_str(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def build_rows(usde, tvl) -> list[dict]:
    """두 계열의 공통 날짜별 값과 30일 변화·신호(감소 = 1)."""
    ud, uv = usde
    td, tv = tvl
    uch, tch = al.change_30d(ud, uv), al.change_30d(td, tv)
    tmap = {int(d): (v, c) for d, v, c in zip(td, tv, tch)}
    rows = []
    for d, v, c in zip(ud, uv, uch):
        if int(d) in tmap and np.isfinite(c) and np.isfinite(tmap[int(d)][1]):
            tvl_v, tvl_c = tmap[int(d)]
            rows.append({"date": day_str(int(d)), "usde_supply": f"{v:.6g}", "usde_30d": f"{c:.6f}", "usde_flag": int(c < 0),
                         "tvl": f"{tvl_v:.6g}", "tvl_30d": f"{tvl_c:.6f}", "tvl_flag": int(tvl_c < 0)})
    return rows


def read_log(path: Path = LOG) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_new(rows: list[dict], path: Path = LOG, today: str | None = None) -> int:
    """기록에 없는 날짜만 덧붙인다. 첫 기록이면 '오늘' 이후 날짜만 시작점으로 삼는다(과거 소급 기록 금지)."""
    have = read_log(path)
    today = today or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    if have:                                                    # 이미 기록이 있으면 마지막 기록 이후 날짜만 잇는다(시작일 이전을 소급해 채우지 않는다)
        last = max(r["date"] for r in have)
        new = [r for r in rows if r["date"] > last]
    else:                                                       # 첫 기록: 오늘 이후 값, 없으면 가장 최근 한 건만
        cands = [r for r in rows if r["date"] >= today]
        new = cands or ([max(rows, key=lambda r: r["date"])] if rows else [])
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    first = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if first:
            w.writeheader()
        w.writerows(sorted(new, key=lambda r: r["date"]))
    return len(new)


def cmd_log(alt_cache="data/alt_cache", fetch=None, path: Path = LOG, today=None) -> int:
    series, errors = al.fetch_all(Path(alt_cache), use_cache=False, fetch=fetch)
    if "usde" not in series or "ethena_tvl" not in series:
        print("수집 실패:", errors)
        return 1
    n = append_new(build_rows(series["usde"], series["ethena_tvl"]), path, today)
    allr = read_log(path)
    print(f"새로 기록 {n}건 · 전체 {len(allr)}건 · 최근 {allr[-1]['date']} USDe 30일 {float(allr[-1]['usde_30d']) * 100:+.1f}% · 신호 {'켜짐(ENA ×0.5)' if allr[-1]['usde_flag'] == '1' else '꺼짐'}")
    return 0


def refresh_ena(cache_dir="data/learn_cache", client=None, now_ms=None, max_age_hours=8) -> tuple[bool, str]:
    """평가에 쓰는 ENA 시세 캐시를 이어받기로 갱신한다(공개 API, 키 불필요). 갱신 함수가 요청 실패를 삼켜도 알 수 있도록,
    갱신 뒤 마지막 1시간봉이 max_age_hours 이내인지 확인한다. 실패해도 평가는 기존 캐시로 계속한다."""
    try:
        import time
        import numpy as np
        from ena_bot.exchange import MAINNET, BybitClient
        from ena_bot.learn_data import update_symbol
        cache = Path(cache_dir)
        update_symbol(client or BybitClient(MAINNET), "ENAUSDT", cache)
        f = cache / "ENAUSDT_k60.npy"
        if not f.exists():
            return False, "시세 캐시가 없습니다"
        a = np.load(f)
        if len(a) == 0:
            return False, "시세 캐시가 비어 있습니다"
        age_h = ((now_ms if now_ms is not None else time.time() * 1000) - float(a[-1, 0])) / 3_600_000
        return (True, "") if age_h <= max_age_hours else (False, f"마지막 시세 봉이 {age_h:.0f}시간 전입니다")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:120]


def evaluate(path: Path = LOG, cache_dir="data/learn_cache", log=print) -> dict:
    from ena_bot.config import Config
    from ena_bot.learn_data import load_coin
    from ena_bot.trend import target_positions
    rows = read_log(path)
    if len(rows) < 2:
        return {"ok": False, "reason": "기록이 2건 미만입니다. --log 로 기록을 시작하세요."}
    cfg = Config()
    t = cfg.trend
    bpy = 365 * 24 * 60 / t.trend_interval_min
    cost = cfg.costs.taker_fee + cfg.costs.slippage_bps / 1e4
    fund = cfg.costs.funding_rate_per_8h * (t.trend_interval_min / 480.0)
    cd = load_coin("ENAUSDT", Path(cache_dir))
    ts, c = cd.bars[:, 0], cd.bars[:, 4]
    base = target_positions(c, cfg)
    start_ms = int(dt.datetime.fromisoformat(rows[0]["date"]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + DAY   # 첫 기록 날짜가 끝난 다음 날부터
    dates = np.array([int(dt.datetime.fromisoformat(r["date"]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000) for r in rows], dtype=np.int64)
    out = {"ok": True, "start": rows[0]["date"], "last_bar": day_str(int(ts[-1])), "variants": {}}
    for key, label in (("usde_flag", "A4 USDe 유통량"), ("tvl_flag", "A5 Ethena TVL")):
        flag = np.array([float(r[key]) for r in rows])
        on = np.nan_to_num(al.asof_known(dates, flag, ts + al.BAR_MS), nan=0.0) > 0
        m = np.where(on, 0.5, 1.0)
        mask = ts >= start_ms
        if mask.sum() < 30:
            out["variants"][key] = {"label": label, "bars": int(mask.sum()), "note": "평가할 봉이 아직 30개 미만(약 5일)입니다"}
            continue
        nb = lab.coin_net(c, base, cost, fund)[mask]
        no = lab.coin_net(c, base * m, cost, fund)[mask]
        sb, so = lab.stats(nb, bpy), lab.stats(no, bpy)
        rb, ro = float((np.cumprod(1 + nb)[-1] - 1) * 100), float((np.cumprod(1 + no)[-1] - 1) * 100)
        out["variants"][key] = {"label": label, "bars": int(mask.sum()), "days": float(mask.sum() * t.trend_interval_min / 1440),
                                "ret_base": rb, "ret_overlay": ro, "mdd_base": sb["mdd"], "mdd_overlay": so["mdd"],
                                "sharpe_base": sb["sharpe"], "sharpe_overlay": so["sharpe"], "on_pct": float(on[mask].mean() * 100)}
    days = max((v.get("days", 0) for v in out["variants"].values()), default=0)
    out["days"] = days
    out["stage"] = "표본 부족" if days < MID_DAYS else "중간 점검(90일 이상 — 아직 판정하지 않음)" if days < FINAL_DAYS else "최종 판정 가능(180일 이상)"
    if days >= FINAL_DAYS:
        for v in out["variants"].values():
            if "ret_base" in v:
                ok = v["ret_overlay"] >= v["ret_base"] and v["mdd_overlay"] <= v["mdd_base"] and v["sharpe_overlay"] > v["sharpe_base"]
                v["final"] = "계속 관찰(그림자 계좌 편입 검토)" if ok else "폐기"
    return out


def render(out: dict) -> str:
    if not out.get("ok"):
        return out["reason"]
    L = [f"기록 시작 {out['start']} · ENA 시세 마지막 봉 {out['last_bar']} · 경과 {out['days']:.0f}일 → {out['stage']}"]
    for v in out["variants"].values():
        if "ret_base" not in v:
            L.append(f"  {v['label']}: {v['note']}")
            continue
        L.append(f"  {v['label']}: 수익률 {v['ret_base']:+.1f}% → {v['ret_overlay']:+.1f}% · 최대 낙폭 {v['mdd_base']:.1f}% → {v['mdd_overlay']:.1f}% · 샤프 {v['sharpe_base']:.2f} → {v['sharpe_overlay']:.2f} · 신호 켜진 시간 {v['on_pct']:.0f}%"
                 + (f"  ⇒ {v['final']}" if "final" in v else ""))
    L.append("읽는 법: 180일 전에는 표본이 작아 우연과 구분되지 않습니다. 판정은 수익률≥, 낙폭≤, 샤프> 를 모두 만족해야 '계속 관찰'입니다(규칙은 ena_round4_prereg.md).")
    return "\n".join(L)


def cmd_daily(path: Path = LOG, out: Path = EVAL_OUT, fetch=None, refresh=refresh_ena, evaluate_fn=None, today=None, alt_cache="data/alt_cache") -> int:
    """기록 → ENA 시세 갱신 → 평가를 순서대로 하고 평가 결과를 파일에 덮어쓴다. 기록에 실패하면(출처 장애 등) 평가하지 않고 1 을 돌려준다."""
    rc = cmd_log(alt_cache=alt_cache, fetch=fetch, path=path, today=today)
    if rc:
        return rc
    ok, why = refresh()
    res = (evaluate_fn or evaluate)(path)
    text = render(res)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note = "" if ok else f"\n⚠ ENA 시세 갱신 실패({why}) — 기존 캐시로 평가했습니다. 위의 'ENA 시세 마지막 봉'을 확인하세요."
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"[{stamp} 갱신]\n{text}{note}\n", encoding="utf-8")
    print(text + note)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--daily", action="store_true")
    a = ap.parse_args(argv)
    if a.daily:
        return cmd_daily()
    if a.log:
        return cmd_log()
    if a.eval:
        print(render(evaluate()))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
