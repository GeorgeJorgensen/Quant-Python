"""Week 4B — Market-Making Simulator V2.
Educational PAPER trading only; no real orders.
pip install yfinance pandas numpy
python assignment_4b.py
python assignment_4b.py --demo
python assignment_4b.py --test

Customer flow is random, fills are assumed at simulated quotes, and historical
Yahoo closes do NOT represent the order book. Not an executable backtest.
"""
from __future__ import annotations
import argparse
import math
import random
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

SYMBOLS = ("AAPL","MSFT","NVDA","AMZN","GOOGL","META","AVGO","TSLA","BRK-B","JPM")

@dataclass(frozen=True)
class Settings:
    capital: float = 100_000.0
    per_name_pct: float = .10
    gross_pct: float = .50
    max_shares: int = 5_000
    loss_limit: float = 1_000.
    min_spread_bps: float = 4.
    max_spread_bps: float = 100.
    vol_multiplier: float = .20
    skew_bps: float = 20.
    tick: float = .01
    lookback: int = 30
    cost_per_share: float = .005

def calculate_order_size(capital, price, position_pct, inventory, side, share_limit=5000):
    """Week 4A risk sizing. Maker buys ADD shares; sells REMOVE shares."""
    if capital <= 0 or price <= 0 or not 0 <= position_pct <= 1:
        raise ValueError("Bad position sizing arguments")
    allowed = min(share_limit, math.floor(capital*position_pct/price))
    if side == "BUY":
        return max(0, allowed-inventory)
    if side == "SELL":
        return max(0, allowed+inventory)
    raise ValueError("Invalid maker side")

@dataclass
class Book:
    symbol: str
    mid: float
    shares: int = 0
    cost_basis: float = 0.
    realized: float = 0.
    fees: float = 0.
    history: list = field(default_factory=list)

    def update(self, price, config):
        """Keep just enough prices for rolling per-bar log-return volatility."""
        if math.isfinite(price) and price > 0:
            self.mid = float(price)
            self.history.append(self.mid)
            self.history = self.history[-(config.lookback+1):]

    def volatility(self):
        if len(self.history) < 3:
            return 0.
        return float(np.std(np.diff(np.log(self.history)), ddof=1))

    def quote(self, config):
        """Long inventory shifts both quotes DOWN; short shifts them UP."""
        limit = max(1, min(config.max_shares,
                           math.floor(config.capital*config.per_name_pct/self.mid)))
        ratio = max(-1., min(1., self.shares/limit))
        reservation = self.mid*(1-ratio*config.skew_bps/10000)
        # A half-spread is half the distance between ASK and BID.
        half = max(self.mid*config.min_spread_bps/20000,
                   self.mid*self.volatility()*config.vol_multiplier)
        half = min(half, self.mid*config.max_spread_bps/20000)
        bid = max(config.tick, round(round((reservation-half)/config.tick)*config.tick, 4))
        ask = max(round(bid+config.tick, 4),
                  round(round((reservation+half)/config.tick)*config.tick, 4))
        return bid, ask

    def fill(self, delta, price, config):
        """Use average-cost accounting for long, short, and reversal fills."""
        old = self.shares
        new = old+delta
        if old == 0 or old*delta > 0:
            size = abs(old)+abs(delta)
            self.cost_basis = (abs(old)*self.cost_basis+abs(delta)*price)/size
        else:
            closed = min(abs(old), abs(delta))
            self.realized += closed*(price-self.cost_basis)*(1 if old>0 else -1)
            if new == 0:
                self.cost_basis = 0.
            elif old*new < 0:
                self.cost_basis = price
        self.shares = new
        self.fees += abs(delta)*config.cost_per_share

    @property
    def unrealized(self):
        return self.shares*(self.mid-self.cost_basis)

    @property
    def pnl(self):
        return self.realized+self.unrealized-self.fees

class Simulator:
    def __init__(self, data, config=Settings(), seed=42):
        self.cfg = config
        # Missing observations remain missing, not fabricated quotes.
        self.data = data.sort_index().replace([np.inf,-np.inf], np.nan)
        self.data = self.data.where(self.data>0)
        self.books = {}
        for name in self.data.columns:
            history = self.data[name].dropna()
            if not history.empty:
                self.books[name] = Book(name, float(history.iloc[0]))
        if not self.books:
            raise ValueError("No valid stock prices")
        self.random = random.Random(seed)
        self.fills = []
        self.rejections = {}
        self.equity = []

    @property
    def gross(self):
        """Gross NOTIONAL exposure sums abs(long AND short positions)."""
        return sum(abs(b.shares*b.mid) for b in self.books.values())

    @property
    def pnl(self):
        return sum(b.pnl for b in self.books.values())

    def reject(self, reason):
        self.rejections[reason] = self.rejections.get(reason, 0)+1
        return False

    def attempt(self, timestamp, name, customer_side, quantity):
        """Pretrade checks evaluate PROPOSED portfolio exposure."""
        if quantity<=0 or customer_side not in ("BUY","SELL"):
            raise ValueError("Invalid customer order")
        book = self.books[name]
        # Customer BUY lifts our ASK: maker sells and inventory decreases.
        maker_side = "SELL" if customer_side=="BUY" else "BUY"
        delta = -quantity if maker_side=="SELL" else quantity
        after = book.shares+delta
        if quantity > calculate_order_size(self.cfg.capital, book.mid,
                    self.cfg.per_name_pct, book.shares, maker_side,
                    self.cfg.max_shares):
            return self.reject("Per-name position limit")
        projected_gross = self.gross-abs(book.shares*book.mid)+abs(after*book.mid)
        if projected_gross > self.cfg.gross_pct*self.cfg.capital+1e-8:
            return self.reject("Portfolio gross exposure")
        # Even after the stop, allow inventory-reducing orders.
        if self.pnl <= -self.cfg.loss_limit and abs(after)>=abs(book.shares):
            return self.reject("Loss limit")
        bid, ask = book.quote(self.cfg)
        fill_price = ask if customer_side=="BUY" else bid
        book.fill(delta, fill_price, self.cfg)
        self.fills.append(dict(timestamp=str(timestamp), symbol=name,
             maker_side=maker_side, shares=quantity, fill=fill_price,
             resulting_inventory=book.shares, fee=quantity*self.cfg.cost_per_share))
        return True

    def run(self, order_count=200):
        """Simulated customers arrive on random historical 5-minute bars."""
        if order_count <= 0:
            raise ValueError("order_count must be positive")
        rows = list(self.data.iterrows())
        schedule = sorted(self.random.randrange(len(rows)) for _ in range(order_count))
        pointer = 0
        for i,(timestamp,row) in enumerate(rows):
            # Mark ALL symbols before evaluating portfolio risk or orders.
            available = []
            for name,value in row.items():
                if name in self.books and pd.notna(value):
                    self.books[name].update(float(value),self.cfg)
                    available.append(name)
            while pointer<len(schedule) and schedule[pointer]==i:
                if available:
                    self.attempt(timestamp,self.random.choice(available),
                        self.random.choice(("BUY","SELL")),
                        self.random.choice((10,25,50,100)))
                pointer+=1
            self.equity.append(dict(timestamp=str(timestamp), net_pnl=self.pnl,
                                    gross_exposure=self.gross))

    def dashboard(self):
        """One row per stock, including risk utilization and fee-adjusted P&L."""
        output=[]
        for name,b in sorted(self.books.items()):
            bid,ask=b.quote(self.cfg)
            limit=max(1,min(self.cfg.max_shares,
                     math.floor(self.cfg.capital*self.cfg.per_name_pct/b.mid)))
            output.append(dict(Symbol=name,Mid=round(b.mid,2),
                Bid=bid,Ask=ask,Vol_bps=round(b.volatility()*10000,2),
                Shares=b.shares,Notional=round(b.shares*b.mid,2),
                Risk_util_pct=round(abs(b.shares)/limit*100,1),
                Realized=round(b.realized,2),Unrealized=round(b.unrealized,2),
                Fees=round(b.fees,2),Net_PnL=round(b.pnl,2)))
        table=pd.DataFrame(output)
        print("\nWEEK 4B — PAPER MARKET MAKING DASHBOARD")
        print(table.to_string(index=False))
        print("Accepted:",len(self.fills),"Rejected:",sum(self.rejections.values()),
              self.rejections)
        print(f"Gross exposure: ${self.gross:,.2f}; Net P&L after fees: ${self.pnl:,.2f}")
        print("Simulated fills are NOT historical executions.")
        return table

def demo_prices():
    """Synthetic data for reproducible unit tests and offline study."""
    rng=np.random.default_rng(10)
    dates=pd.date_range("2026-01-05 09:30",periods=150,freq="5min")
    starts=(250,500,135,210,180,730,300,350,480,300)
    return pd.DataFrame({symbol: p*np.exp(np.cumsum(rng.normal(0,.0015,len(dates))))
        for symbol,p in zip(SYMBOLS,starts)},index=dates)

def yahoo_prices():
    import yfinance as yf
    raw=yf.download(list(SYMBOLS),period="5d",interval="5m",
                    auto_adjust=True,group_by="column",progress=False)
    if raw.empty:
        raise RuntimeError("Yahoo returned no data; try --demo")
    if isinstance(raw.columns,pd.MultiIndex):
        close=raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw.xs("Close",axis=1,level=1)
    else:
        close=raw[["Close"]].rename(columns={"Close":SYMBOLS[0]})
    if isinstance(close,pd.Series):
        close=close.to_frame(name=SYMBOLS[0])
    close.columns=[str(s).upper() for s in close.columns]
    return close

def tests():
    cfg=Settings()
    assert calculate_order_size(10000,100,.1,4,"BUY")==6
    b=Book("TEST",100)
    b.update(100,cfg)
    neutral=b.quote(cfg)
    b.shares=50
    long=b.quote(cfg)
    b.shares=-50
    short=b.quote(cfg)
    assert long[0]<neutral[0]<short[0]
    assert long[1]<neutral[1]<short[1]
    b=Book("TEST",100)
    b.fill(10,100,cfg)
    b.fill(-15,110,cfg)
    assert (b.shares,b.cost_basis,b.realized)==(-5,110,100)
    b.mid=100
    assert b.unrealized==50
    sim=Simulator(demo_prices(),cfg)
    sim.run(100)
    assert len(sim.fills)+sum(sim.rejections.values())==100
    assert sim.gross<=cfg.gross_pct*cfg.capital+1e-6
    print("All Week 4B tests passed")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--demo",action="store_true")
    p.add_argument("--test",action="store_true")
    p.add_argument("--orders",type=int,default=200)
    p.add_argument("--export",action="store_true")
    a=p.parse_args()
    if a.test:
        tests()
        return
    data=demo_prices() if a.demo else yahoo_prices()
    print("Data:", "SYNTHETIC" if a.demo else "Yahoo historical bars")
    sim=Simulator(data)
    sim.run(a.orders)
    dashboard=sim.dashboard()
    if a.export:
        dashboard.to_csv("week4b_dashboard.csv",index=False)
        pd.DataFrame(sim.fills).to_csv("week4b_trades.csv",index=False)
        pd.DataFrame(sim.equity).to_csv("week4b_equity.csv",index=False)

if __name__=="__main__":
    main()
