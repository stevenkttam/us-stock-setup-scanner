from __future__ import annotations
from typing import Any
import numpy as np
import pandas as pd

def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x))) if np.isfinite(x) else 0.0

def score_volume(rvol: float) -> float:
    if not np.isfinite(rvol): return 0.0
    return clamp(np.interp(rvol,[1.0,1.5,2.0,2.5,3.0],[25,55,75,90,100]))

def score_candidate(row: pd.Series,direction: str,n: int,regime: str,signal_cfg: dict|None=None)->dict[str,Any]|None:
    cfg=signal_cfg or {}; range_max=float(cfg.get("consolidation_range_max",.12)); atr_ratio_max=float(cfg.get("atr10_atr40_max",.85)); breakout_min=float(cfg.get("breakout_atr_min",.25)); rvol_min=float(cfg.get("rvol20_min",1.20)); loc_long=float(cfg.get("close_location_long_min",.65)); loc_short=float(cfg.get("close_location_short_max",.35)); range_exp=float(cfg.get("range_expansion_tr_min",1.50))
    close,atr=row.get("Close"),row.get("ATR20")
    if not all(np.isfinite([close,atr])) or atr<=0:return None
    resistance,support=row.get(f"HH_{n}"),row.get(f"LL_{n}"); rng=row.get(f"RangePct_{n}"); a10,a40=row.get("ATR10",np.nan),row.get("ATR40",np.nan)
    comp=a10/a40 if np.isfinite(a10) and np.isfinite(a40) and a40>0 else np.nan; rvol,loc=row.get("RVOL20",np.nan),row.get("CloseLocation",np.nan)
    if not np.isfinite(rng) or rng>range_max or not np.isfinite(comp) or comp>atr_ratio_max or not np.isfinite(rvol) or rvol<rvol_min:return None
    if direction=="Long":
        if not np.isfinite(resistance) or close<=resistance or not np.isfinite(loc) or loc<loc_long:return None
        br=(close-resistance)/atr
        if br<breakout_min:return None
        trend=np.mean([100 if close>row.get("EMA20",np.nan) else 0,100 if row.get("EMA20",np.nan)>row.get("EMA50",np.nan) else 0,100 if row.get("EMA50",np.nan)>row.get("EMA200",np.nan) else 0]); rs=clamp(50+row.get("RS_SPY_20",0)*1000); label="Strong" if trend>=80 else "Mixed"
        setup="VCP / Volatility Contraction Breakout" if rng<=.08 and comp<=.75 else ("Range Expansion" if row.get("TR_ATR20",0)>=range_exp else ("Trend Continuation" if trend>=80 else "Consolidation Breakout"))
        why=f"{n}d base + {rvol:.1f}x RVOL + {br:.1f} ATR breakout + {label.lower()} trend"; stop=max(row.get(f"LL_{n}",close-2*atr)-.25*atr,close-1.5*atr); risk=close-stop; target=close+2*risk
    else:
        if not np.isfinite(support) or close>=support or not np.isfinite(loc) or loc>loc_short:return None
        br=(support-close)/atr
        if br<breakout_min:return None
        trend=np.mean([100 if close<row.get("EMA20",np.nan) else 0,100 if row.get("EMA20",np.nan)<row.get("EMA50",np.nan) else 0,100 if row.get("EMA50",np.nan)<row.get("EMA200",np.nan) else 0]); rs=clamp(50-row.get("RS_SPY_20",0)*1000); label="Strong" if trend>=80 else "Mixed"
        setup="VCP / Volatility Contraction Breakdown" if rng<=.08 and comp<=.75 else ("Range Expansion Down" if row.get("TR_ATR20",0)>=range_exp else ("Trend Breakdown" if trend>=80 else "Consolidation Breakdown"))
        why=f"{n}d base + {rvol:.1f}x RVOL + {br:.1f} ATR breakdown + {label.lower()} trend"; stop=min(row.get(f"HH_{n}",close+2*atr)+.25*atr,close+1.5*atr); risk=stop-close; target=close-2*risk
    consolidation=clamp(100-(rng/range_max)*100); volatility=clamp((1-comp)*300); market=80 if (("Bullish" in str(regime) and direction=="Long") or ("Bearish" in str(regime) and direction=="Short")) else 45; breakout=clamp(50+br*35)
    score=.20*breakout+.20*score_volume(rvol)+.15*consolidation+.15*trend+.10*volatility+.10*rs+.10*market
    return {"Direction":direction,"Setup":setup,"SetupScore":round(score,1),"RVOL20":round(float(rvol),2),"BaseDays":int(n),"BreakoutATR":round(float(br),2),"Trend":label,"ATRCompression":round(float(1-comp),3),"Trend20":float(row.get("Trend20",np.nan)),"Trend50":float(row.get("Trend50",np.nan)),"Trend200":float(row.get("Trend200",np.nan)),"RS_SPY_20":float(row.get("RS_SPY_20",np.nan)),"TR_ATR20":float(row.get("TR_ATR20",np.nan)),"Entry":float(close),"Stop":float(stop),"Target2R":float(target),"RiskPerShare":float(risk),"MarketRegime":str(regime),"Why":why}

def scan_symbol(df:pd.DataFrame,ticker:str,regime:str,config:dict)->tuple[list[dict],list[dict]]:
    latest=df.iloc[-1]; longs=[]; shorts=[]
    for n in config["signal"]["consolidation_lengths"]:
        for direction,store in [("Long",longs),("Short",shorts)]:
            result=score_candidate(latest,direction,n,regime,config["signal"])
            if result: result["Ticker"]=ticker; store.append(result)
    def best(items): return sorted(items,key=lambda x:x["SetupScore"],reverse=True)[0] if items else None
    return ([best(longs)] if best(longs) else [],[best(shorts)] if best(shorts) else [])
