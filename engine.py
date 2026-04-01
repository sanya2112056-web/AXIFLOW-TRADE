"""
AXIFLOW — Quant Engine
Smart Money: OI, Funding, Liquidations, CVD, OrderBook, AMD/FVG
Scans market continuously — fires signal when conditions are met
"""
import asyncio, time, random, logging
from dataclasses import dataclass, field
import httpx

log = logging.getLogger("axiflow.engine")
BFUT = "https://fapi.binance.com"

SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","LINKUSDT",
    "LTCUSDT","MATICUSDT","ATOMUSDT","UNIUSDT","NEARUSDT",
]


@dataclass
class Signal:
    symbol:     str
    decision:   str
    confidence: int
    strategy:   str
    score:      float
    entry:      float
    tp:         float
    sl:         float
    rr:         float
    lev:        int
    reasons:    list
    raw:        dict
    ts:         float = field(default_factory=time.time)

    def to_dict(self):
        return {
            "symbol":self.symbol,"decision":self.decision,
            "confidence":self.confidence,"strategy":self.strategy,
            "score":round(self.score,2),"entry":self.entry,
            "tp":round(self.tp,6),"sl":round(self.sl,6),
            "rr":round(self.rr,2),"lev":self.lev,
            "reasons":self.reasons,"raw":self.raw,"ts":self.ts,
        }


async def _get(c, url, params=None):
    try:
        r = await c.get(url, params=params, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"HTTP {url}: {e}")
        return None


async def fetch_ticker(c, sym):
    d = await _get(c, f"{BFUT}/fapi/v1/ticker/24hr", {"symbol":sym})
    if not d: return {"price":0.0,"change":0.0,"volume":0.0}
    return {"price":float(d["lastPrice"]),"change":float(d["priceChangePercent"]),"volume":float(d["quoteVolume"])}

async def fetch_klines(c, sym, tf="5m", limit=80):
    d = await _get(c, f"{BFUT}/fapi/v1/klines", {"symbol":sym,"interval":tf,"limit":limit})
    if not d: return _mock(limit)
    return [{"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4]),"v":float(x[5]),"t":int(x[0])} for x in d]

async def fetch_oi(c, sym):
    now  = await _get(c, f"{BFUT}/fapi/v1/openInterest", {"symbol":sym})
    hist = await _get(c, f"{BFUT}/futures/data/openInterestHist", {"symbol":sym,"period":"5m","limit":6})
    cur  = float(now["openInterest"]) if now else random.uniform(50000,120000)
    vals = [float(h["sumOpenInterest"]) for h in (hist or [])]
    p15  = vals[-1] if len(vals)>=4 else cur*0.995
    d15  = (cur-p15)/max(p15,1)*100
    return {"current":cur,"delta_15m":d15,"strength":2 if abs(d15)>5 else 1 if abs(d15)>2 else 0}

async def fetch_funding(c, sym):
    d  = await _get(c, f"{BFUT}/fapi/v1/premiumIndex", {"symbol":sym})
    fr = float(d["lastFundingRate"]) if d else random.uniform(-0.003,0.007)
    return {"rate":fr,"extreme_long":fr>0.01,"extreme_short":fr<-0.01}

async def fetch_liqs(c, sym):
    d = await _get(c, f"{BFUT}/fapi/v1/allForceOrders", {"symbol":sym,"limit":100})
    if not d:
        lv,sv = random.uniform(10000,80000),random.uniform(10000,80000)
    else:
        lv = sum(float(o["origQty"])*float(o["price"]) for o in d if o.get("side")=="SELL")
        sv = sum(float(o["origQty"])*float(o["price"]) for o in d if o.get("side")=="BUY")
    r = lv/max(sv,1)
    return {"long":lv,"short":sv,"ratio":r,"strength":2 if r>3 or r<0.33 else 1 if r>2 or r<0.5 else 0}

async def fetch_ob(c, sym):
    d = await _get(c, f"{BFUT}/fapi/v1/depth", {"symbol":sym,"limit":20})
    if not d:
        bv,av = random.uniform(500,1500),random.uniform(500,1500)
    else:
        bv = sum(float(b[1]) for b in d.get("bids",[]))
        av = sum(float(a[1]) for a in d.get("asks",[]))
    t  = bv+av; imb = (bv-av)/t if t else 0
    return {"bid":bv,"ask":av,"imbalance":imb,"strength":2 if abs(imb)>0.2 else 1 if abs(imb)>0.1 else 0}


def compute_cvd(candles):
    cum=0.0
    for c in candles[:-1]: cum += c["v"] if c["c"]>=c["o"] else -c["v"]
    prev=cum; last=candles[-1]
    cum += last["v"] if last["c"]>=last["o"] else -last["v"]
    pd = 1 if len(candles)>1 and last["c"]>candles[-2]["c"] else -1
    cd = 1 if cum>prev else -1
    div = 1 if cd>0 and pd<0 else -1 if cd<0 and pd>0 else 0
    return {"divergence":div}

def compute_atr(candles, period=14):
    if len(candles)<period+1: return candles[-1]["h"]-candles[-1]["l"] if candles else 1
    trs=[]
    for i in range(1,min(period+1,len(candles))):
        c=candles[-i]; p=candles[-i-1]
        trs.append(max(c["h"]-c["l"],abs(c["h"]-p["c"]),abs(c["l"]-p["c"])))
    return sum(trs)/len(trs)

def vol_ratio(candles):
    if len(candles)<10: return 1.0
    avg=sum(c["v"] for c in candles[:-5])/max(len(candles[:-5]),1)
    rec=sum(c["v"] for c in candles[-5:])/5
    return rec/max(avg,0.001)

def detect_amd(candles, oi_delta):
    if len(candles)<15: return {"active":False,"confirmed":False}
    recent=candles[-15:]
    highs=[c["h"] for c in recent]; lows=[c["l"] for c in recent]; vols=[c["v"] for c in recent]
    pr=(max(highs)-min(lows))/max(min(lows),1)*100
    if pr>=2.0: return {"active":False,"confirmed":False}
    rt,rb=max(highs),min(lows); tol=pr*0.15
    if sum(1 for h in highs if abs(h-rt)/max(rt,1)*100<tol)<2 and sum(1 for l in lows if abs(l-rb)/max(rb,1)*100<tol)<2:
        return {"active":False,"confirmed":False}
    if not (vols[-1]<vols[0] and 0<=oi_delta<=3): return {"active":True,"confirmed":False}
    avg_v=sum(vols[:-3])/max(len(vols[:-3]),1); avg_s=sum(c["h"]-c["l"] for c in recent[:-3])/max(len(recent[:-3]),1)
    for c in candles[-3:]:
        bt=c["h"]>rt*1.002; bb=c["l"]<rb*0.998; back=rb<candles[-1]["c"]<rt
        spike=c["v"]>avg_v*1.5 or (c["h"]-c["l"])>avg_s*1.5
        if (bt or bb) and back and spike:
            return {"active":True,"confirmed":True,"fake":"up" if bt else "down",
                    "signal":"SHORT" if bt else "LONG","fvg_top":rt,"fvg_bot":rb}
    return {"active":True,"confirmed":False}


def score_market(ticker, oi, funding, liqs, ob, cvd_data):
    s=0.0; reasons=[]
    d=oi["delta_15m"]; up=ticker["change"]>0
    if   d>5:           s+=1.5; reasons.append(f"OI екстрем +{d:.1f}% — агресивне відкриття")
    elif d>2 and up:    s+=1.0; reasons.append(f"OI +{d:.1f}% + ціна ↑ — накопичення")
    elif d>2 and not up:s-=1.0; reasons.append(f"OI +{d:.1f}% + ціна ↓ — шорт тиск")
    elif d<-2 and up:   s-=0.5; reasons.append(f"OI −{abs(d):.1f}% слабкий ріст")
    fr=funding["rate"]
    if funding["extreme_long"]:  s-=1.0; reasons.append(f"Funding перекупленість {fr*100:.4f}%")
    elif funding["extreme_short"]:s+=1.0; reasons.append(f"Funding перепроданість {fr*100:.4f}%")
    r=liqs["ratio"]
    if   r>2:   s+=1.0; reasons.append(f"Лонг ліквідації x{r:.1f} → розворот ↑")
    elif r<0.5: s-=1.0; reasons.append(f"Шорт ліквідації x{1/max(r,.001):.1f} → розворот ↓")
    im=ob["imbalance"]
    if   im>0.15: s+=1.0; reasons.append(f"OB тиск покупців {im:+.2f}")
    elif im<-0.15:s-=1.0; reasons.append(f"OB тиск продавців {im:+.2f}")
    dv=cvd_data["divergence"]
    if   dv==1:  s+=1.0; reasons.append("CVD бичача дивергенція")
    elif dv==-1: s-=1.0; reasons.append("CVD ведмежа дивергенція")
    return s, reasons

def calc_confidence(final, oi_s, liq_s, ob_s, amd_conf):
    return min(100,max(0,int(50+abs(final)*12+oi_s*5+liq_s*5+ob_s*5+(10 if amd_conf else 0))))

def calc_tp_sl(price, direction, atr_val):
    risk=atr_val*1.5; reward=risk*4.0
    if direction=="LONG": return price+reward, price-risk
    return price-reward, price+risk

def calc_lev(conf, final):
    if conf>=80 and abs(final)>=4: return 5
    if conf>=72 and abs(final)>=3: return 3
    if conf>=65: return 2
    return 1

def build_signal(sym, ticker, oi, funding, liqs, ob, cvd_data, amd, candles, vr):
    price=ticker["price"]
    base,reasons=score_market(ticker,oi,funding,liqs,ob,cvd_data)
    amd_score=0.0; strategy="STANDARD"
    if amd.get("confirmed"):
        amd_score=3.5 if amd["signal"]=="LONG" else -3.5
        strategy="AMD_FVG"
        reasons.append(f"⚡ AMD: фейк {amd['fake'].upper()} → {amd['signal']}")
        reasons.append(f"FVG зона ${amd['fvg_bot']:.2f}–${amd['fvg_top']:.2f}")
    final=base+amd_score
    raw={"price":price,"change":ticker["change"],"oi":oi["delta_15m"],
         "funding":funding["rate"],"liq":liqs["ratio"],"ob":ob["imbalance"],
         "amd":amd.get("confirmed",False),"vol":vr}
    if vr<0.7 and not amd.get("confirmed"):
        return Signal(sym,"NO TRADE",0,strategy,final,price,price,price,0,1,[f"Об'єм низький {vr:.0%}"],raw)
    if abs(final)<1.5 and not amd.get("confirmed"):
        return Signal(sym,"NO TRADE",0,strategy,final,price,price,price,0,1,[f"Score {final:.1f} нижче мінімуму"],raw)
    if   final>=2:  decision="LONG"
    elif final<=-2: decision="SHORT"
    else:           decision="NO TRADE"
    conf=calc_confidence(final,oi["strength"],liqs["strength"],ob["strength"],amd.get("confirmed",False))
    atr_val=compute_atr(candles)
    tp,sl=calc_tp_sl(price,decision,atr_val) if decision!="NO TRADE" else (price,price)
    rr=abs(tp-price)/max(abs(price-sl),0.0001) if decision!="NO TRADE" else 0
    lev=calc_lev(conf,final) if decision!="NO TRADE" else 1
    return Signal(sym,decision,conf,strategy,final,price,tp,sl,rr,lev,reasons or ["Без причин"],raw)


class AxiflowEngine:
    def __init__(self):
        self.cache:   dict[str,Signal] = {}
        self.history: dict[str,list]   = {}
        self._prev:   dict[str,float]  = {}

    async def analyze(self, sym: str) -> Signal:
        try:
            async with httpx.AsyncClient() as c:
                ticker,candles,oi,funding,liqs,ob = await asyncio.gather(
                    fetch_ticker(c,sym), fetch_klines(c,sym,"5m",80),
                    fetch_oi(c,sym), fetch_funding(c,sym),
                    fetch_liqs(c,sym), fetch_ob(c,sym),
                )
            cvd_data=compute_cvd(candles); amd=detect_amd(candles,oi["delta_15m"]); vr=vol_ratio(candles)
            sig=build_signal(sym,ticker,oi,funding,liqs,ob,cvd_data,amd,candles,vr)
            self.cache[sym]=sig
            self.history.setdefault(sym,[]).append(sig)
            if len(self.history[sym])>50: self.history[sym]=self.history[sym][-50:]
            log.info(f"[{sym}] {sig.decision} conf={sig.confidence}% score={sig.score:.1f}")
            return sig
        except Exception as e:
            log.error(f"Engine {sym}: {e}")
            return Signal(sym,"NO TRADE",0,"STANDARD",0,0,0,0,0,1,["API помилка"],{"price":0})

    async def analyze_all(self, syms=None) -> dict:
        syms=syms or SYMBOLS
        results=await asyncio.gather(*[self.analyze(s) for s in syms],return_exceptions=True)
        return {s:(r if isinstance(r,Signal) else Signal(s,"NO TRADE",0,"STANDARD",0,0,0,0,0,1,["Error"],{"price":0}))
                for s,r in zip(syms,results)}

    def is_new_signal(self, sym: str) -> bool:
        sig=self.cache.get(sym)
        if not sig or sig.decision=="NO TRADE": self._prev[sym]=0; return False
        prev=self._prev.get(sym,0); curr=sig.score
        fired=(prev==0 and abs(curr)>=2) or (abs(curr-prev)>=1.5)
        self._prev[sym]=curr
        return fired

    def get(self, sym): return self.cache.get(sym)

def _mock(n=80):
    p=67000.0; out=[]
    for _ in range(n):
        o=p; c=o+random.uniform(-200,200)
        out.append({"o":o,"h":max(o,c)+random.uniform(0,80),"l":min(o,c)-random.uniform(0,80),"c":c,"v":random.uniform(100,800)})
        p=c
    return out
