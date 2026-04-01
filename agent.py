"""
AXIFLOW — Autonomous Trading Agent
Continuously scans market, fires on signal, executes real trades via Bybit/Binance
Sends Telegram notifications for every event
"""
import asyncio, time, logging, os
from dataclasses import dataclass, field
from typing import Optional
import httpx

from core.engine import AxiflowEngine, Signal, SYMBOLS

log = logging.getLogger("axiflow.agent")

SCAN_FAST = 30    # seconds between quick scans
SCAN_DEEP = 300   # seconds between full scans of all pairs


@dataclass
class Trade:
    id:       str
    symbol:   str
    side:     str   # BUY / SELL
    entry:    float
    tp:       float
    sl:       float
    amount:   float
    leverage: int
    opened:   float = field(default_factory=time.time)
    pnl:      float = 0.0
    status:   str   = "open"  # open / tp / sl / manual


class ExchangeClient:
    """Supports Bybit + Binance via CCXT"""
    def __init__(self, bybit_key="", bybit_secret="",
                 binance_key="", binance_secret="", testnet=True):
        self.testnet = testnet
        self.demo    = not bybit_key and not binance_key
        self._bybit  = None
        self._binance= None
        if bybit_key:   self._init_bybit(bybit_key, bybit_secret)
        if binance_key: self._init_binance(binance_key, binance_secret)
        log.info(f"Exchange: bybit={'✓' if self._bybit else '✗'} "
                 f"binance={'✓' if self._binance else '✗'} "
                 f"testnet={testnet} demo={self.demo}")

    def _init_bybit(self, key, secret):
        try:
            import ccxt
            self._bybit = ccxt.bybit({"apiKey":key,"secret":secret,"enableRateLimit":True})
            if self.testnet: self._bybit.set_sandbox_mode(True)
        except Exception as e: log.error(f"Bybit init: {e}")

    def _init_binance(self, key, secret):
        try:
            import ccxt
            self._binance = ccxt.binanceusdm({
                "apiKey":key,"secret":secret,"enableRateLimit":True,
                "options":{"defaultType":"future"}
            })
            if self.testnet: self._binance.set_sandbox_mode(True)
        except Exception as e: log.error(f"Binance init: {e}")

    def _exchange(self):
        return self._bybit or self._binance

    async def get_balance(self) -> float:
        if self.demo: return 10000.0
        try:
            import asyncio as aio
            ex = self._exchange()
            bal = await aio.to_thread(ex.fetch_balance)
            return float(bal.get("USDT",{}).get("free",0))
        except Exception as e:
            log.error(f"Balance: {e}"); return 0.0

    async def place_order(self, symbol:str, side:str, usdt:float,
                          leverage:int, tp:float, sl:float) -> dict:
        if self.demo:
            return {"id":f"DEMO_{int(time.time())}","status":"filled",
                    "symbol":symbol,"side":side,"amount":usdt,
                    "tp":tp,"sl":sl,"demo":True}
        try:
            import asyncio as aio
            ex = self._exchange()
            sym_fmt = symbol.replace("USDT","/USDT:USDT")
            # Set leverage
            try: await aio.to_thread(ex.set_leverage, leverage, sym_fmt)
            except: pass
            # Get price
            ticker = await aio.to_thread(ex.fetch_ticker, sym_fmt)
            price  = ticker["last"]
            qty    = round(usdt / price, 6)
            # Market order
            order  = await aio.to_thread(
                ex.create_market_order, sym_fmt, side.lower(), qty
            )
            # Set TP/SL
            close_side = "sell" if side=="BUY" else "buy"
            try:
                await aio.to_thread(ex.create_order, sym_fmt,
                    "take_profit_market", close_side, qty, tp,
                    {"stopPrice":tp,"closePosition":True,"reduceOnly":True})
                await aio.to_thread(ex.create_order, sym_fmt,
                    "stop_market", close_side, qty, sl,
                    {"stopPrice":sl,"closePosition":True,"reduceOnly":True})
            except Exception as e:
                log.warning(f"TP/SL not set: {e}")
            return {"id":order["id"],"status":"filled","symbol":symbol,
                    "side":side,"amount":usdt,"tp":tp,"sl":sl,"demo":False}
        except Exception as e:
            log.error(f"Order failed {symbol}: {e}")
            return {"error":str(e)}


class TradingAgent:
    def __init__(self, engine: AxiflowEngine, exchange: ExchangeClient,
                 tg_token:str, tg_chat:str,
                 risk_pct:float=1.5, min_conf:int=70, max_open:int=3):
        self.engine    = engine
        self.ex        = exchange
        self.tg_token  = tg_token
        self.tg_chat   = tg_chat
        self.risk_pct  = risk_pct
        self.min_conf  = min_conf
        self.max_open  = max_open
        self.running   = False
        self.trades:   list[Trade] = []
        self.closed:   list[Trade] = []
        self.total_pnl = 0.0
        self.scans     = 0
        self._last_deep= 0.0

    async def start(self):
        self.running = True
        log.info("Agent started")
        await self._notify(
            "🟢 *AXIFLOW Agent запущено*\n\n"
            f"📊 Сканую {len(SYMBOLS)} пар\n"
            f"⚡ Мін. конфіденційність: {self.min_conf}%\n"
            f"💰 Ризик: {self.risk_pct}% на угоду\n"
            f"📐 RR = 1:4 на кожну угоду\n"
            f"🏦 Режим: {'Demo' if self.ex.demo else 'Live'}"
        )
        while self.running:
            try:
                now = time.time()
                is_deep = (now - self._last_deep) >= SCAN_DEEP
                if is_deep:
                    await self._deep_scan()
                    self._last_deep = now
                else:
                    await self._fast_scan()
                await self._monitor_trades()
                self.scans += 1
            except Exception as e:
                log.error(f"Agent loop: {e}")
            await asyncio.sleep(SCAN_FAST)

    def stop(self):
        self.running = False

    async def _deep_scan(self):
        """Full scan of all pairs — every 5 min"""
        log.info("Deep scan...")
        sigs = await self.engine.analyze_all(SYMBOLS)
        hits = [s for s in sigs.values() if s.decision!="NO TRADE" and s.confidence>=self.min_conf]
        # Summary message
        lines = []
        for sym in SYMBOLS:
            s = sigs.get(sym)
            if not s: continue
            e = "🟢" if s.decision=="LONG" else "🔴" if s.decision=="SHORT" else "⚪"
            lines.append(f"{e} `{sym}` {s.decision} {s.confidence}%")
        if lines:
            await self._notify("📊 *Скан ринку*\n\n" + "\n".join(lines))
        for sig in hits:
            await self._try_trade(sig)

    async def _fast_scan(self):
        """Quick scan — re-analyze only if signal looks promising"""
        for sym in SYMBOLS:
            cached = self.engine.get(sym)
            # Already open on this pair — skip
            if any(t.symbol==sym and t.status=="open" for t in self.trades): continue
            # Re-analyze
            sig = await self.engine.analyze(sym)
            if sig.decision != "NO TRADE" and sig.confidence >= self.min_conf:
                if self.engine.is_new_signal(sym):
                    await self._try_trade(sig)

    async def _try_trade(self, sig: Signal):
        open_count = sum(1 for t in self.trades if t.status=="open")
        if open_count >= self.max_open: return
        if any(t.symbol==sig.symbol and t.status=="open" for t in self.trades): return

        balance  = await self.ex.get_balance()
        risk_usd = balance * (self.risk_pct/100)
        pos_size = risk_usd * sig.lev

        log.info(f"Opening {sig.decision} {sig.symbol} ${pos_size:.0f} lev={sig.lev}x conf={sig.confidence}%")

        order = await self.ex.place_order(
            symbol=sig.symbol,
            side="BUY" if sig.decision=="LONG" else "SELL",
            usdt=pos_size, leverage=sig.lev,
            tp=sig.tp, sl=sig.sl,
        )
        if "error" in order:
            await self._notify(f"❌ Помилка ордера `{sig.symbol}`:\n{order['error']}")
            return

        trade = Trade(
            id=order["id"], symbol=sig.symbol,
            side="BUY" if sig.decision=="LONG" else "SELL",
            entry=sig.entry, tp=sig.tp, sl=sig.sl,
            amount=pos_size, leverage=sig.lev,
        )
        self.trades.append(trade)

        demo = " _(demo)_" if order.get("demo") else ""
        msg = (
            f"{'🟢' if sig.decision=='LONG' else '🔴'} "
            f"*{sig.decision} відкрито{demo}*\n\n"
            f"📌 `{sig.symbol}`\n"
            f"💲 Вхід: `${sig.entry:,.4f}`\n"
            f"🎯 TP: `${sig.tp:,.4f}`\n"
            f"🛑 SL: `${sig.sl:,.4f}`\n"
            f"📐 RR: `1:{sig.rr:.1f}`\n"
            f"⚡ Плече: `{sig.lev}x`\n"
            f"💰 Позиція: `${pos_size:.0f}`\n"
            f"🧠 Стратегія: `{sig.strategy}`\n"
            f"📊 Конф.: `{sig.confidence}%`\n\n"
            + "\n".join(f"• {r}" for r in sig.reasons[:4])
        )
        await self._notify(msg)

    async def _monitor_trades(self):
        open_trades = [t for t in self.trades if t.status=="open"]
        if not open_trades: return
        async with httpx.AsyncClient() as c:
            from core.engine import fetch_ticker
            for t in open_trades:
                try:
                    ticker = await fetch_ticker(c, t.symbol)
                    price  = ticker["price"]
                    if t.side=="BUY":
                        pnl_pct = (price-t.entry)/t.entry*100*t.leverage
                        hit_tp  = price>=t.tp; hit_sl = price<=t.sl
                    else:
                        pnl_pct = (t.entry-price)/t.entry*100*t.leverage
                        hit_tp  = price<=t.tp; hit_sl = price>=t.sl
                    t.pnl = t.amount*pnl_pct/100
                    if hit_tp:
                        t.status="tp"; self.total_pnl+=t.pnl; self.closed.append(t)
                        await self._notify(
                            f"✅ *TAKE PROFIT!*\n\n"
                            f"📌 `{t.symbol}` {t.side}\n"
                            f"💰 PnL: `+${t.pnl:.2f}` (+{pnl_pct:.1f}%)\n"
                            f"📈 Загальний PnL: `${self.total_pnl:.2f}`"
                        )
                    elif hit_sl:
                        t.status="sl"; self.total_pnl+=t.pnl; self.closed.append(t)
                        await self._notify(
                            f"🛑 *STOP LOSS*\n\n"
                            f"📌 `{t.symbol}` {t.side}\n"
                            f"💸 PnL: `${t.pnl:.2f}` ({pnl_pct:.1f}%)\n"
                            f"📉 Загальний PnL: `${self.total_pnl:.2f}`"
                        )
                except Exception as e:
                    log.warning(f"Monitor {t.symbol}: {e}")

    async def _notify(self, text: str):
        if not self.tg_token or not self.tg_chat:
            log.info(f"[TG] {text[:80]}")
            return
        try:
            async with httpx.AsyncClient() as c:
                await c.post(
                    f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                    json={"chat_id":self.tg_chat,"text":text,
                          "parse_mode":"Markdown","disable_web_page_preview":True},
                    timeout=5,
                )
        except Exception as e:
            log.warning(f"TG notify: {e}")

    def stats(self) -> dict:
        open_t  = [t for t in self.trades if t.status=="open"]
        closed_t= self.closed
        wins    = [t for t in closed_t if t.pnl>0]
        return {
            "running":     self.running,
            "scans":       self.scans,
            "open_count":  len(open_t),
            "closed_count":len(closed_t),
            "win_rate":    round(len(wins)/max(len(closed_t),1)*100,1),
            "total_pnl":   round(self.total_pnl,2),
            "open_trades": [{"id":t.id,"symbol":t.symbol,"side":t.side,
                             "entry":t.entry,"tp":t.tp,"sl":t.sl,
                             "pnl":round(t.pnl,2),"lev":t.leverage,
                             "amount":round(t.amount,2)} for t in open_t],
            "closed_trades":[{"symbol":t.symbol,"side":t.side,
                              "pnl":round(t.pnl,2),"status":t.status} for t in closed_t[-10:]],
        }
