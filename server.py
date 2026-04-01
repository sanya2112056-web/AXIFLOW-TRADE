"""AXIFLOW — FastAPI Server"""
import asyncio, os, time, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from core.engine import AxiflowEngine, SYMBOLS
from core.agent  import TradingAgent, ExchangeClient

log = logging.getLogger("axiflow.server")

# ── Globals ──────────────────────────────────────────────
engine  = AxiflowEngine()
agent: Optional[TradingAgent] = None
_agent_task = None
wallets: dict = {}
manual_trades: list = []

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN","")
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID","")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("AXIFLOW server starting...")
    asyncio.create_task(engine.analyze_all(SYMBOLS))
    asyncio.create_task(_refresh_loop())
    yield

async def _refresh_loop():
    while True:
        try: await engine.analyze_all(SYMBOLS)
        except Exception as e: log.error(f"Refresh: {e}")
        await asyncio.sleep(60)

app = FastAPI(title="AXIFLOW", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")


# ── Models ────────────────────────────────────────────────
class WalletReq(BaseModel):
    user_id:        str
    exchange:       str = "demo"
    bybit_key:      str = ""
    bybit_secret:   str = ""
    binance_key:    str = ""
    binance_secret: str = ""
    testnet:        bool = True
    balance:        float = 10000.0

class AgentReq(BaseModel):
    user_id:   str
    action:    str
    risk_pct:  float = 1.5
    min_conf:  int   = 70
    max_open:  int   = 3

class TradeReq(BaseModel):
    user_id:  str
    symbol:   str
    side:     str
    amount:   float
    leverage: int = 1


# ── Routes ────────────────────────────────────────────────
@app.get("/")
async def root(): return {"status":"ok","service":"AXIFLOW"}

@app.get("/api/signals")
async def all_signals():
    return {"signals":{s:sig.to_dict() for s,sig in engine.cache.items()},"ts":time.time()}

@app.get("/api/signal/{symbol}")
async def one_signal(symbol:str, fresh:bool=False):
    sym=symbol.upper()
    sig = await engine.analyze(sym) if fresh or not engine.get(sym) else engine.get(sym)
    return sig.to_dict()

@app.get("/api/market/{symbol}")
async def market_data(symbol:str):
    import httpx as hx
    from core.engine import fetch_ticker,fetch_oi,fetch_funding,fetch_liqs,fetch_ob
    sym=symbol.upper()
    async with hx.AsyncClient() as c:
        t,oi,fr,lq,ob = await asyncio.gather(
            fetch_ticker(c,sym),fetch_oi(c,sym),fetch_funding(c,sym),
            fetch_liqs(c,sym),fetch_ob(c,sym)
        )
    return {"symbol":sym,"price":t["price"],"change":t["change"],
            "oi":oi,"funding":fr,"liquidations":lq,"orderbook":ob,"ts":time.time()}

@app.get("/api/klines/{symbol}")
async def klines(symbol:str, interval:str="5m", limit:int=80):
    import httpx as hx
    from core.engine import fetch_klines
    sym=symbol.upper()
    async with hx.AsyncClient() as c:
        candles=await fetch_klines(c,sym,interval,limit)
    return {"symbol":sym,"interval":interval,"candles":candles}

@app.post("/api/wallet")
async def save_wallet(req: WalletReq):
    ex = ExchangeClient(
        bybit_key=req.bybit_key, bybit_secret=req.bybit_secret,
        binance_key=req.binance_key, binance_secret=req.binance_secret,
        testnet=req.testnet,
    )
    bal = await ex.get_balance()
    wallets[req.user_id] = {"exchange":req.exchange,"balance":bal or req.balance,
                             "testnet":req.testnet,"demo":ex.demo,"connected":True,"client":ex}
    return {"success":True,"balance":bal or req.balance,"demo":ex.demo}

@app.get("/api/wallet/{user_id}")
async def get_wallet(user_id:str):
    w=wallets.get(user_id,{})
    return {k:v for k,v in w.items() if k!="client"} if w else {"connected":False}

@app.post("/api/agent")
async def control_agent(req: AgentReq):
    global agent, _agent_task
    w = wallets.get(req.user_id)
    ex = w["client"] if w and "client" in w else ExchangeClient()

    if req.action=="start":
        if agent and agent.running: return {"success":False,"msg":"Агент вже запущений"}
        agent = TradingAgent(engine=engine, exchange=ex,
                             tg_token=TG_TOKEN, tg_chat=TG_CHAT,
                             risk_pct=req.risk_pct, min_conf=req.min_conf, max_open=req.max_open)
        _agent_task = asyncio.create_task(agent.start())
        return {"success":True,"msg":"Агент запущений"}

    elif req.action=="stop":
        if agent: agent.stop()
        return {"success":True,"msg":"Агент зупинений"}

    elif req.action=="status":
        if not agent: return {"running":False,"scans":0,"open_count":0,"win_rate":0,"total_pnl":0,"open_trades":[],"closed_trades":[]}
        return agent.stats()

    return {"success":False,"msg":"Unknown action"}

@app.post("/api/trade")
async def manual_trade(req: TradeReq):
    w = wallets.get(req.user_id)
    ex = w["client"] if w and "client" in w else ExchangeClient()
    sig = engine.get(req.symbol.upper())
    tp = sig.tp if sig else 0
    sl = sig.sl if sig else 0
    order = await ex.place_order(req.symbol.upper(), req.side, req.amount, req.leverage, tp, sl)
    t = {"id":order.get("id","?"),"symbol":req.symbol,"side":req.side,
         "amount":req.amount,"leverage":req.leverage,"tp":tp,"sl":sl,
         "ts":time.time(),"demo":order.get("demo",True)}
    manual_trades.append(t)
    return {"success":True,"trade":t}

@app.get("/api/trades/{user_id}")
async def get_trades(user_id:str):
    return {"trades":manual_trades[-30:]}

if __name__=="__main__":
    import uvicorn
    uvicorn.run("api.server:app",host="0.0.0.0",port=int(os.environ.get("PORT",8000)),reload=False)
