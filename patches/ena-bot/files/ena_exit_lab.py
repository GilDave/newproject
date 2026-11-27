"""익절·트레일링 같은 '청산 규칙'을 현재 추세 전략에 덧씌우면 좋아지는지 과거 데이터로 시험한다(읽기 전용, 매매·설정은 건드리지 않는다).

기준 시뮬레이터는 ena_bot.trend.run_trend_backtest 와 같은 비용·펀딩 모델이며, 시작할 때 두 결과가 일치하는지 먼저 출력한다.
각 규칙은 전체 기간과 전반·후반으로 나눠 보여 준다: 전체에서만 좋고 한쪽 반에서 나쁘면 우연일 가능성이 크다.
실행(ENA 폴더에서):  .venv/bin/python ena_exit_lab.py [시세 CSV]
"""
import sys, numpy as np
sys.path.insert(0, ".")
from ena_bot.config import Config
from ena_bot.data import Candles
from ena_bot.trend import resample_candles, target_positions, _apply_threshold, run_trend_backtest
import csv

def load(path):
    rows=[r for r in csv.reader(open(path)) if r and r[0].replace('.','',1).isdigit()]
    a=np.array([[float(x) for x in r[:6]] for r in rows])
    return Candles(a[:,0].astype(np.int64),a[:,1],a[:,2],a[:,3],a[:,4],a[:,5])

cfg=Config(); t=cfg.trend
c60=load((sys.argv[1] if len(sys.argv) > 1 else "data/ENAUSDT_60m.csv"))
factor=t.trend_interval_min//t.base_interval_min
c=resample_candles(c60,factor,t.base_interval_min)
close=c.close; n=len(close)
bars_per_year=365*24*60/t.trend_interval_min
cost_side=cfg.costs.taker_fee+cfg.costs.slippage_bps/1e4
fund=cfg.costs.funding_rate_per_8h*(t.trend_interval_min/480.0)

def sim(pos, lo=0, hi=None):
    pos=_apply_threshold(pos,t.rebalance_threshold)
    ret=np.r_[0.0,np.diff(close)/close[:-1]]
    held=np.r_[0.0,pos[:-1]]
    eq=cfg.initial_equity; qty=0.0; net=np.zeros(n); equity=np.zeros(n); turn=np.zeros(n)
    for i in range(n):
        if i>0:
            px=close[i-1]; tn=held[i]*eq; turn[i]=abs(tn-qty*px)/eq; qty=tn/px
        net[i]=max(held[i]*ret[i]-cost_side*turn[i]-abs(held[i])*fund,-0.999)
        eq*=1+net[i]; equity[i]=eq
    hi=hi or n
    r=net[lo:hi]; e=np.cumprod(1+r)
    pk=np.maximum.accumulate(e); mdd=((pk-e)/pk).max()*100
    sh=r.mean()/r.std()*np.sqrt(bars_per_year) if r.std()>0 else 0
    return dict(ret=(e[-1]-1)*100, mdd=mdd, sharpe=sh, turn=turn[lo:hi].sum()/((hi-lo)/bars_per_year))

base=target_positions(close,cfg)
ret=np.r_[0.0,np.diff(close)/close[:-1]]
vol=np.full(n,np.nan)
for i in range(t.vol_window-1,n): vol[i]=ret[i-t.vol_window+1:i+1].std()

def overlay_tp(zhi, nbar, keep):
    """방향으로 nbar 동안 zhi 시그마 이상 벌어졌으면 노출을 keep 배로 줄이고, z<=0 이 되면 복귀."""
    m=np.ones(n); cut=False
    for i in range(nbar,n):
        s=np.sign(base[i]);
        if s==0 or np.isnan(vol[i]): cut=False; continue
        z=s*(close[i]/close[i-nbar]-1)/(vol[i]*np.sqrt(nbar))
        if not cut and z>=zhi: cut=True
        elif cut and z<=0: cut=False
        m[i]=keep if cut else 1.0
    return base*m

def overlay_trail(k, wait):
    """진입 후 유리한 극값에서 k*시그마*sqrt(6) 되돌림이면 평탄화, wait 봉 뒤 신호가 같으면 재진입."""
    pos=base.copy(); side=0; ext=0.0; flat_until=-1
    for i in range(n):
        s=np.sign(base[i])
        if s!=side: side=s; ext=close[i]; flat_until=-1
        if side==0 or np.isnan(vol[i]): continue
        ext=max(ext,close[i]) if side>0 else min(ext,close[i])
        if i<flat_until: pos[i]=0; continue
        draw=side*(ext-close[i])/ext
        if draw>=k*vol[i]*np.sqrt(6) and flat_until<i:
            flat_until=i+wait; pos[i]=0; ext=close[i]
    return pos

if __name__=="__main__":
    ref=run_trend_backtest(c60,cfg)
    b=sim(base)
    print("bars",n,"검증: 백테스트 수익",round(ref["total_return_pct"],2),"/ 내 시뮬",round(b["ret"],2),"| mdd",round(ref["max_drawdown_pct"],2),round(b["mdd"],2))
    half=n//2
    def show(name,pos):
        a,h1,h2=sim(pos),sim(pos,0,half),sim(pos,half,n)
        print(f"{name:28s} 전체 {a['ret']:+8.1f}% mdd {a['mdd']:5.1f} sh {a['sharpe']:+.2f} 회전 {a['turn']:5.1f} | 전반 {h1['ret']:+7.1f}% sh {h1['sharpe']:+.2f} | 후반 {h2['ret']:+7.1f}% sh {h2['sharpe']:+.2f}")
    show("기준(현재 전략)",base)
    for z in (1.5,2.0,3.0):
        for nb in (6,12):
            for keep in (0.5,0.0):
                show(f"익절 z>={z} n={nb} keep={keep}",overlay_tp(z,nb,keep))
    for k in (2,3,4):
        for w in (6,18):
            show(f"트레일 k={k} 대기={w}봉",overlay_trail(k,w))
