"""DESK OPTIONS MM V7 — an event-driven options market-making research simulator.

This is PUBLIC-DATA / SYNTHETIC research software, NOT an implementation of
Citadel Securities technology. No broker, API key, execution gateway, or live
trading connection exists. See README for data, limitations and assumptions.

Study sequence: instruments -> American pricer -> volatility marks -> quote
engine -> passive execution -> portfolio checks -> delayed delta hedge -> KPIs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta, time, timezone
from pathlib import Path
from statistics import NormalDist
from zoneinfo import ZoneInfo

import numpy as np

N = NormalDist()
NY = ZoneInfo('America/New_York')
UTC = timezone.utc


def dtparse(value: str) -> datetime:
    """Reject naive timestamps; event ordering requires one unambiguous clock."""
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Input timestamps MUST have UTC offset, e.g. +00:00')
    return parsed.astimezone(UTC)


def annual_time(now: datetime, expiry: date) -> float:
    """Assumption: US equity options expire at 16:00 Eastern on expiry date.

    Actual last-trade/exercise cutoff differs by contract and venue. Contract
    calendar and settlement must be provided by a licensed reference-data feed.
    """
    end = datetime.combine(expiry, time(16, 0), NY).astimezone(UTC)
    return max(0.0, (end - now).total_seconds() / (365.0 * 24 * 3600))


@dataclass(frozen=True)
class Contract:
    id: str
    expiry: date
    strike: float
    right: str
    multiplier: int = 100

    def __post_init__(self):
        if not self.id or self.strike <= 0 or self.right not in ('C', 'P') or self.multiplier < 1:
            raise ValueError('Invalid contract')


@dataclass(frozen=True)
class ModelGreeks:
    price: float
    delta: float
    gamma: float
    vega: float  # option price sensitivity per +1.00 (100 vol points)


def euro_price(spot: float, strike: float, years: float, vol: float,
               r: float, q: float, right: str) -> float:
    """Analytic European benchmark; useful for American-model cross-checks."""
    if years <= 0:
        return max(spot-strike, 0.0) if right == 'C' else max(strike-spot, 0.0)
    d1 = (math.log(spot/strike)+(r-q+vol*vol/2)*years)/(vol*math.sqrt(years))
    d2 = d1-vol*math.sqrt(years)
    return (spot*math.exp(-q*years)*N.cdf(d1)-strike*math.exp(-r*years)*N.cdf(d2)) if right == 'C' else (strike*math.exp(-r*years)*N.cdf(-d2)-spot*math.exp(-q*years)*N.cdf(-d1))


def american_price(spot: float, strike: float, years: float, vol: float,
                   r: float, q: float, right: str, steps: int = 64) -> float:
    """Cox-Ross-Rubinstein binomial price with early exercise at each node.

    ASSUMPTION: dividends are a continuous yield q, NOT actual discrete
    ex-dividend cashflows. US stock options may be exercised early. Real desk
    pricing needs exchange calendars, dividends, borrow, settlement and more.
    """
    if spot <= 0 or strike <= 0 or vol <= 0 or steps < 4 or right not in ('C', 'P'):
        raise ValueError('Invalid option pricing input')
    intrinsic = max(spot-strike, 0.0) if right == 'C' else max(strike-spot, 0.0)
    if years <= 1e-9:
        return intrinsic
    h = years / steps
    u = math.exp(vol*math.sqrt(h))
    d = 1/u
    p = (math.exp((r-q)*h)-d)/(u-d)
    if not 0 < p < 1:
        raise ValueError('CRR tree probability invalid; revise time step / model')
    discount = math.exp(-r*h)
    j = np.arange(steps+1, dtype=float)
    terminal = spot*(u**j)*(d**(steps-j))
    value = np.maximum(terminal-strike,0) if right == 'C' else np.maximum(strike-terminal,0)
    for level in range(steps-1, -1, -1):
        value = discount*(p*value[1:]+(1-p)*value[:-1])
        k = np.arange(level+1)
        node_spots = spot*(u**k)*(d**(level-k))
        exercise = np.maximum(node_spots-strike,0) if right == 'C' else np.maximum(strike-node_spots,0)
        value = np.maximum(value,exercise)
    return float(value[0])


def model(spot: float, c: Contract, tau: float, iv: float, rate: float,
          yield_: float, steps: int) -> ModelGreeks:
    """Finite-difference Greeks for the American tree (not analytically exact).

    Delta ~ dV/dS; gamma ~ d^2 V/dS^2; vega ~ dV/dsigma.
    Market quoting and portfolio risk use the SAME repricing function.
    """
    f = lambda s, v: american_price(s,c.strike,tau,v,rate,yield_,c.right,steps)
    p = f(spot,iv)
    # A 2% spot bump smooths CRR lattice artifacts; tiny bumps give a noisy
    # gamma on a finite tree. Sensitivity to bump size must be validated.
    ds = max(.25,spot*.02)
    dv = .01
    up, down = f(spot+ds,iv), f(spot-ds,iv)
    return ModelGreeks(p,(up-down)/(2*ds),(up-2*p+down)/ds**2,
                       (f(spot,iv+dv)-f(spot,max(.005,iv-dv)))/(iv+dv-max(.005,iv-dv)))


def invert_american_iv(market_mid: float, spot: float, c: Contract,
                       tau: float, r: float, q: float, steps: int) -> float | None:
    """Bisection-based American implied volatility from OBSERVED option mid.

    Reject prices that don't fit model bounds instead of silently forcing IV.
    """
    if market_mid <= 0 or tau <= 0:
        return None
    low, high = .03, 2.5
    try:
        lo = american_price(spot,c.strike,tau,low,r,q,c.right,steps)
        hi = american_price(spot,c.strike,tau,high,r,q,c.right,steps)
        if not (lo-1e-6 <= market_mid <= hi+1e-6):
            return None
        for _ in range(12):
            center = (low+high)/2
            if american_price(spot,c.strike,tau,center,r,q,c.right,steps) < market_mid:
                low = center
            else:
                high = center
        return (low+high)/2
    except ValueError:
        return None


@dataclass(frozen=True)
class Config:
    rate: float = .04
    yield_: float = .005
    tree_steps: int = 48
    min_half_spread: float = .035
    model_uncertainty_pct: float = .012
    inventory_skew_dollars: float = .09
    market_tick: float = .01
    order_size: int = 3
    max_per_contract: int = 16
    max_abs_delta: float = 650.
    max_abs_gamma: float = 120.
    max_abs_vega: float = 32_000.
    max_gross_option_notional: float = 110_000.
    max_stock_shares: int = 2_000
    max_loss: float = 4_500.
    max_stale_seconds: float = 10.
    hedge_trigger: float = 85.
    max_hedge_per_event: int = 100
    stock_slippage_bps: float = 1.5
    option_fee: float = .65
    stock_fee_per_share: float = .005
    latency_ms: int = 15
    ahead_fraction: float = .35
    # When our quote improves the NBBO, public prints are insufficient evidence
    # that OUR specific order got hit. Randomly capture a fraction of plausible
    # inside-spread prints; this remains an uncalibrated modeling assumption.
    inside_capture_probability: float = .30
    max_stress_loss: float = 12_000.
    seed: int = 29

    def __post_init__(self):
        if self.tree_steps < 8 or self.market_tick <= 0 or self.max_stale_seconds <= 0 or not 0 <= self.inside_capture_probability <= 1:
            raise ValueError('Invalid pricing / market configuration')


@dataclass
class MarketQuote:
    time: datetime
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    iv: float | None = None


@dataclass
class WorkingQuote:
    time: datetime
    active_at: datetime
    bid: float
    ask: float
    remaining_bid: int
    remaining_ask: int
    ahead_bid: int
    ahead_ask: int


@dataclass
class Fill:
    time: str
    contract: str
    maker_side: str
    quantity: int
    price: float
    observed_mid: float
    next_quote_markout: float | None = None


class OptionsDesk:
    def __init__(self, contracts: list[Contract], cfg: Config = Config()):
        if not contracts or len({x.id for x in contracts}) != len(contracts):
            raise ValueError('Need distinct contracts')
        self.contracts = {c.id:c for c in contracts}
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)  # Deterministic execution sampling per path.
        self.positions = {c.id:0 for c in contracts}
        self.market: dict[str,MarketQuote] = {}
        self.working: dict[str,WorkingQuote] = {}
        self.cash = 0.
        self.stock_shares = 0
        self.underlying_bid: float | None = None
        self.underlying_ask: float | None = None
        self.underlying_time: datetime | None = None
        self.now: datetime | None = None
        self.pending_hedge = 0
        self.killed = False
        self.fills: list[Fill] = []
        self.hedges: list[dict] = []
        self.risk_history: list[dict] = []
        self.rejects: dict[str,int] = {}
        self.event_count = 0
        self.iv_surface_points = 0
        self._last_marks: dict[str,float] = {}
        # Invalidate when underlying/option market data changes, NOT on fills.
        self._greek_cache: dict[str, ModelGreeks] = {}

    @property
    def spot(self):
        if self.underlying_bid is None or self.underlying_ask is None:
            raise ValueError('No underlying quote')
        return (self.underlying_bid+self.underlying_ask)/2

    def rejection(self, reason):
        self.rejects[reason] = self.rejects.get(reason,0)+1
        return False

    def fresh(self, quote_time):
        return self.now is not None and 0 <= (self.now-quote_time).total_seconds() <= self.cfg.max_stale_seconds

    def surface_iv(self, contract: Contract):
        """Causal cross-sectional shrinkage using only current/past quote IVs.

        Fit IV = a + b*log(K/S) + c*log(K/S)^2 + d*sqrt(T) with
        ridge regularization. This is NOT a static-arbitrage-free SVI surface.
        A production surface would constrain calendar and butterfly arbitrage.
        """
        obs = []
        for cid,quote in self.market.items():
            c = self.contracts[cid]
            tau = annual_time(self.now,c.expiry)
            if not self.fresh(quote.time) or quote.iv is None or tau <= 0:
                continue
            x = math.log(c.strike/self.spot)
            obs.append((x,math.sqrt(tau),quote.iv))
        if not obs:
            return None
        self.iv_surface_points = len(obs)
        if len(obs) < 5:
            # Avoid fitting underdetermined polynomial surfaces.
            direct = self.market.get(contract.id)
            return direct.iv if direct and direct.iv is not None else float(np.median([o[2] for o in obs]))
        x = np.asarray([[1., z, z*z, t] for z,t,_ in obs])
        y = np.asarray([iv for _,_,iv in obs])
        penalty = np.diag([.001, .1, .5, .1])
        coefficients = np.linalg.solve(x.T @ x+penalty,x.T @ y)
        z = math.log(contract.strike/self.spot)
        tau = annual_time(self.now,contract.expiry)
        fitted = float(np.array([1.,z,z*z,math.sqrt(max(tau,0))]) @ coefficients)
        return float(np.clip(fitted,.05,1.50))

    def theo(self, contract: Contract):
        if contract.id in self._greek_cache:
            return self._greek_cache[contract.id]
        iv = self.surface_iv(contract)
        tau = annual_time(self.now,contract.expiry)
        if iv is None or tau <= 0:
            return None
        try:
            result = model(self.spot,contract,tau,iv,self.cfg.rate,
                         self.cfg.yield_,self.cfg.tree_steps)
            self._greek_cache[contract.id] = result
            return result
        except ValueError:
            return None

    def greeks(self, override=None):
        """Use full revaluation of each existing position, signed by quantity."""
        delta = float(self.stock_shares)
        gamma = vega = gross = 0.
        for cid,c in self.contracts.items():
            qty = self.positions[cid] + (override[1] if override and override[0]==cid else 0)
            if qty == 0:
                continue
            t = self.theo(c)
            if t is None:
                # Conservative: a risk-unknown position is not a valid new trade.
                raise ValueError('Cannot price existing exposure; cancel quotes')
            shares = qty*c.multiplier
            delta += shares*t.delta
            gamma += shares*t.gamma
            vega += shares*t.vega
            gross += abs(shares*t.price)
        return {'delta':delta,'gamma':gamma,'vega':vega,'gross':gross}

    def marked_pnl(self, liquidation=False):
        """Cash + marked option liabilities + stock inventory.

        Liquidation uses OBSERVED option bid for long positions, ask for short;
        theoretical/IV-derived prices would overstate exit liquidity.
        Missing or stale marks -> None, never substitute zero for unknown risk.
        """
        if self.underlying_bid is None:
            return None
        equity = self.cash + self.stock_shares*(
            self.underlying_bid if liquidation and self.stock_shares>0 else
            self.underlying_ask if liquidation and self.stock_shares<0 else self.spot)
        for cid,qty in self.positions.items():
            if not qty:
                continue
            quote = self.market.get(cid)
            if quote is None or not self.fresh(quote.time):
                return None
            mark = (quote.bid if qty>0 else quote.ask) if liquidation else (quote.bid+quote.ask)/2
            equity += qty*self.contracts[cid].multiplier*mark
        return equity

    @staticmethod
    def approximate_stress(risks, spot):
        """Fast Delta-Gamma-Vega scenario P&L for a PREFILL safety bound.

        This Taylor approximation can be quite wrong under large shocks; the
        full repricing stress() report is a separate, better scenario model.
        """
        losses=[]
        for underlying_shock, vol_shock in ((-.05,.1),(.05,.1),(-.1,.2),(.1,.2),
                                            (-.05,-.1),(.05,-.1)):
            ds=spot*underlying_shock
            estimated=risks['delta']*ds+.5*risks['gamma']*ds*ds+risks['vega']*vol_shock
            losses.append(estimated)
        return min(losses)

    def limits_ok(self,cid,qty):
        """Hard pre-fill portfolio limits, reject risk that grows beyond caps.

        Crucial: if an existing market move ALREADY breaches a limit, only
        incremental trades reducing that breached metric are allowed.
        """
        old = self.positions[cid]
        if abs(old+qty)>self.cfg.max_per_contract:
            return self.rejection('per_contract')
        marked = self.marked_pnl(liquidation=True)
        if marked is None:
            return self.rejection('missing_or_stale_marks')
        if marked < -self.cfg.max_loss:
            self.killed=True
        if self.killed and abs(old+qty)>=abs(old):
            return self.rejection('kill_switch')
        try:
            before=self.greeks()
            after=self.greeks((cid,qty))
        except ValueError:
            return self.rejection('unknown_greeks')
        for key,limit in (('delta',self.cfg.max_abs_delta),
                          ('gamma',self.cfg.max_abs_gamma),
                          ('vega',self.cfg.max_abs_vega),
                          ('gross',self.cfg.max_gross_option_notional)):
            if abs(after[key])>limit+1e-6 and abs(after[key]) >= abs(before[key])-1e-6:
                return self.rejection('risk_'+key)
        current_stress=self.approximate_stress(before,self.spot)
        new_stress=self.approximate_stress(after,self.spot)
        if new_stress < -self.cfg.max_stress_loss and new_stress <= current_stress+1e-6:
            return self.rejection('approx_stress')
        return True

    def on_underlying(self,t,bid,ask):
        if not 0<bid<ask:
            return self.rejection('invalid_underlying')
        self.now=t
        self._greek_cache.clear()
        # Pending hedge executes at the NEXT underlying event, not the price
        # that was used to decide to trade; avoids zero-latency hedge fantasy.
        if self.pending_hedge:
            qty=self.pending_hedge
            px=(ask if qty>0 else bid)*(1+(1 if qty>0 else -1)*self.cfg.stock_slippage_bps/10_000)
            fee=abs(qty)*self.cfg.stock_fee_per_share
            self.cash-=qty*px+fee
            self.stock_shares+=qty
            self.hedges.append(dict(time=t.isoformat(),shares=qty,price=round(px,5),fees=fee))
            self.pending_hedge=0
        self.underlying_bid,self.underlying_ask=bid,ask
        self.underlying_time=t
        # Old quotes canceled on a new underlying update; next option updates
        # must refresh fair value before a hypothetical customer can trade.
        self.working.clear()

    def on_quote(self,t,cid,bid,ask,bid_size,ask_size):
        self.now=t
        self._greek_cache.clear()
        if cid not in self.contracts or self.underlying_time is None:
            return self.rejection('unknown_option_or_underlying')
        if not self.fresh(self.underlying_time):
            return self.rejection('stale_underlying')
        if bid<=0 or ask<=bid or bid_size<=0 or ask_size<=0:
            return self.rejection('invalid_nbbo')
        c=self.contracts[cid]
        tau=annual_time(t,c.expiry)
        if tau<=0:
            return self.rejection('expired')
        market=MarketQuote(t,bid,ask,bid_size,ask_size)
        market.iv=invert_american_iv((bid+ask)/2,self.spot,c,tau,
                                      self.cfg.rate,self.cfg.yield_,self.cfg.tree_steps)
        self.market[cid]=market
        if market.iv is None:
            self.working.pop(cid,None)
            return self.rejection('invalid_implied_vol')
        m=self.theo(c)
        if m is None:
            self.working.pop(cid,None)
            return self.rejection('no_model')
        # Quote center skews against both local inventory and portfolio delta;
        # long options / excessive positive delta encourage selling.
        inventory=self.positions[cid]/max(1,self.cfg.max_per_contract)
        try:
            portfolio_delta=self.greeks()['delta']
        except ValueError:
            self.working.pop(cid,None)
            return self.rejection('unpriceable_portfolio')
        delta_pressure=np.clip(portfolio_delta/self.cfg.max_abs_delta,-1,1)
        skew=-self.cfg.inventory_skew_dollars*(inventory+.4*delta_pressure)
        half=max(self.cfg.min_half_spread,
                 self.cfg.model_uncertainty_pct*max(m.price,.50)+.05*m.gamma)
        tick=self.cfg.market_tick
        our_bid=round(max(tick,math.floor((m.price+skew-half)/tick)*tick),2)
        our_ask=round(max(our_bid+tick,math.ceil((m.price+skew+half)/tick)*tick),2)
        # No locked/crossed quotes; bid must be strictly below outside ask.
        our_bid=min(our_bid,round(ask-tick,2))
        our_ask=max(our_ask,round(bid+tick,2))
        if our_bid<=0 or our_bid>=our_ask:
            self.working.pop(cid,None)
            return self.rejection('invalid_generated_quote')
        # A quote joining displayed NBBO waits behind a fraction of its size.
        # An inside-spread quote is assumed to have no ahead volume; both are
        # simplifications of exchange priority, hidden size and routing.
        ahead_bid=math.ceil(bid_size*self.cfg.ahead_fraction) if abs(our_bid-bid)<tick/2 else 0
        ahead_ask=math.ceil(ask_size*self.cfg.ahead_fraction) if abs(our_ask-ask)<tick/2 else 0
        self.working[cid]=WorkingQuote(t,t+timedelta(milliseconds=self.cfg.latency_ms),
                                      our_bid,our_ask,self.cfg.order_size,
                                      self.cfg.order_size,ahead_bid,ahead_ask)

    def on_trade(self,t,cid,price,quantity,aggressor):
        """One market print; at-touch fills need queue depletion.

        NOTE: a real PRINT cannot identify our queue position. This is a
        conservative hypothetical fill model, NOT actual venue replay.
        """
        self.now=t
        w=self.working.get(cid)
        m=self.market.get(cid)
        if not w or not m or not self.fresh(w.time) or t<w.active_at:
            return
        if aggressor not in ('BUY','SELL') or quantity<1:
            return self.rejection('bad_print')
        # Don't infer a fill from an off-touch print. A trade at the market
        # ask is a plausible aggressor BUY; bid corresponds to SELL.
        if aggressor=='BUY' and price < m.ask-self.cfg.market_tick/2:
            return self.rejection('trade_not_at_offer')
        if aggressor=='SELL' and price > m.bid+self.cfg.market_tick/2:
            return self.rejection('trade_not_at_bid')
        # Customer BUY executes on maker ASK; SELL on maker BID.
        buying = aggressor=='BUY'
        our_px=w.ask if buying else w.bid
        outside = m.ask if buying else m.bid
        if (buying and our_px>outside+1e-8) or (not buying and our_px<outside-1e-8):
            return
        remaining=w.remaining_ask if buying else w.remaining_bid
        if remaining<=0:
            return
        # Explicit 1:1 competition for printed liquidity with ahead queue.
        ahead_attr='ahead_ask' if buying else 'ahead_bid'
        queue=getattr(w,ahead_attr)
        at_touch=abs(our_px-outside)<self.cfg.market_tick/2
        # An inside quote has priority if routed to the same venue, but a
        # consolidated print alone cannot prove routing/queue participation.
        # This capture probability is a SENSITIVITY parameter, not empirical.
        if not at_touch and self.rng.random() >= self.cfg.inside_capture_probability:
            return
        consumable=max(0,quantity-queue) if at_touch else quantity
        if at_touch:
            setattr(w,ahead_attr,max(0,queue-quantity))
        fills=min(remaining,consumable)
        if not fills:
            return
        signed=-fills if buying else fills
        if not self.limits_ok(cid,signed):
            return
        self.positions[cid]+=signed
        self.cash-=signed*self.contracts[cid].multiplier*our_px
        self.cash-=fills*self.cfg.option_fee
        if buying: w.remaining_ask-=fills
        else: w.remaining_bid-=fills
        self.fills.append(Fill(t.isoformat(),cid,'SELL' if buying else 'BUY',
                               fills,our_px,(m.bid+m.ask)/2))
        # Next market mark for the same contract will measure 1-step adverse
        # selection (maker buy profits if future mid > buy price).
        # The markout is retrospective ANALYTICS, NEVER an input to quoting.

    def after_quote_markout(self,cid,new_market_mid):
        for f in self.fills:
            if f.contract==cid and f.next_quote_markout is None:
                sign=1 if f.maker_side=='BUY' else -1
                f.next_quote_markout=round(sign*(new_market_mid-f.price)*f.quantity*self.contracts[cid].multiplier,4)

    def snapshot(self):
        mark=self.marked_pnl()
        liq=self.marked_pnl(liquidation=True)
        if mark is None:
            return
        try:
            r=self.greeks()
        except ValueError:
            return
        self.risk_history.append(dict(time=self.now.isoformat(),pnl_mid=mark,
             pnl_liquidation=liq,stock=self.stock_shares,**r))
        if liq is not None and liq <= -self.cfg.max_loss:
            self.killed=True
        # Recalculate pending hedge after EVERY new risk snapshot; replace/cancel
        # an earlier instruction if order flow has changed our inventory.
        # Any hedge still executes only on the NEXT underlying market event.
        if abs(r['delta'])>=self.cfg.hedge_trigger:
            target=int(round(-r['delta']))
            capped=max(-self.cfg.max_hedge_per_event,
                       min(self.cfg.max_hedge_per_event,target))
            capped=max(-self.cfg.max_stock_shares-self.stock_shares,
                       min(self.cfg.max_stock_shares-self.stock_shares,capped))
            self.pending_hedge=capped
        else:
            self.pending_hedge=0

    def event(self,row):
        t=dtparse(row['timestamp'])
        if self.now and t < self.now:
            raise ValueError('Events must be time-sorted')
        self.event_count+=1
        kind=row['event']
        if kind=='UNDERLYING':
            self.on_underlying(t,float(row['bid']),float(row['ask']))
        elif kind=='QUOTE':
            cid=row['contract_id']
            if cid in self.market:
                old=self.market[cid]
                # Causal analytics: markouts computed from this NOW-arrived quote.
                self.after_quote_markout(cid,(float(row['bid'])+float(row['ask']))/2)
            self.on_quote(t,cid,float(row['bid']),float(row['ask']),
                          int(row['bid_size']),int(row['ask_size']))
        elif kind=='TRADE':
            self.on_trade(t,row['contract_id'],float(row['price']),
                          int(row['quantity']),row['aggressor'])
        else:
            raise ValueError(f'Unknown event: {kind}')
        # Record fresh risk after quote/trade events. Earlier versions took
        # almost no intraday snapshots, which prevented delayed stock hedges.
        # An underlying event may temporarily make option marks stale; quotes
        # arriving subsequently restore an observable full-portfolio mark.
        if kind in ('QUOTE','TRADE'):
            self.snapshot()

    def run(self,rows):
        for row in rows:
            self.event(row)
        self.snapshot()
        return self

    def stress(self, shocks=((-.05,.1),(.05,.1),(-.10,.2),(.10,.2))):
        """Full repricing under spot and parallel IV shocks, holding inventory.

        Scenario loss compares shifted theoretical holdings to current
        theoretical holdings; no liquidity/vol-surface dynamics or exercise.
        """
        base=0.
        vals=[]
        for cid,c in self.contracts.items():
            if not self.positions[cid]: continue
            iv=self.surface_iv(c)
            if iv is None: continue
            tau=annual_time(self.now,c.expiry)
            base+=self.positions[cid]*c.multiplier*american_price(
                self.spot,c.strike,tau,iv,self.cfg.rate,self.cfg.yield_,c.right,self.cfg.tree_steps)
            vals.append((c,self.positions[cid],iv,tau))
        results=[]
        for ds,dv in shocks:
            scenario_spot=self.spot*(1+ds)
            scenario=self.stock_shares*(scenario_spot-self.spot)
            for c,qty,iv,tau in vals:
                scenario+=qty*c.multiplier*american_price(scenario_spot,c.strike,tau,
                              max(.02,iv+dv),self.cfg.rate,self.cfg.yield_,c.right,self.cfg.tree_steps)
            scenario-=base
            results.append({'spot_shock':ds,'vol_point_shock':dv,'approx_pnl':round(scenario,2)})
        return results

    def report(self):
        last=self.risk_history[-1] if self.risk_history else {}
        marks=[x['pnl_mid'] for x in self.risk_history]
        peak=0.
        dd=0.
        for x in marks:
            peak=max(peak,x)
            dd=min(dd,x-peak)
        markouts=[f.next_quote_markout for f in self.fills if f.next_quote_markout is not None]
        return dict(mode='OFFLINE RESEARCH / NO LIVE ORDERS',events=self.event_count,
          fills=len(self.fills),option_contracts_filled=sum(f.quantity for f in self.fills),
          stock_hedges=len(self.hedges),net_mid_pnl=round(last.get('pnl_mid',0),2),
          liquidation_pnl=round(last.get('pnl_liquidation',0),2),
          max_observed_drawdown=round(dd,2),
          average_first_next_quote_markout=round(float(np.mean(markouts)),2) if markouts else None,
          rejection_counts=self.rejects,kill_switch=self.killed,
          exposures={k:round(last.get(k,0),2) for k in ('delta','gamma','vega','gross')},
          stress=self.stress())

    def export(self,path):
        output=Path(path)
        output.mkdir(parents=True,exist_ok=True)
        for name,records in (('fills',[asdict(x) for x in self.fills]),
                             ('hedges',self.hedges),('risk',self.risk_history)):
            with (output/(name+'.csv')).open('w',newline='') as f:
                if records:
                    w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
        (output/'summary.json').write_text(json.dumps(self.report(),indent=2))


def read_contracts(path):
    with open(path,newline='') as f:
        return [Contract(r['id'],date.fromisoformat(r['expiry']),float(r['strike']),
                         r['right'],int(r.get('multiplier') or 100)) for r in csv.DictReader(f)]


def read_events(path):
    with open(path,newline='') as f:
        yield from csv.DictReader(f)


def demo(seed=29,steps=42):
    """Generate a complete causal input event stream; NO engine forecasts.

    Trades may lean with the NEXT simulated price change (informed flow). This
    information only enters the external market/flow generator, not strategy.
    Rows are generated all at once for convenience but consumed IN TIME ORDER.
    """
    rng=random.Random(seed)
    start=datetime(2026,10,12,14,30,tzinfo=UTC)
    cs=[Contract(f'SYN_{d}_{k}_{right}',date(2026,10,12)+timedelta(days=d),
                 float(k),right) for d in (30,75) for k in (95,100,105) for right in ('C','P')]
    path=[100.]
    for _ in range(steps):
        path.append(path[-1]*math.exp(rng.gauss(0,.002)))
    events=[]
    for i in range(steps):
        t=start+timedelta(seconds=30*i)
        spot=path[i]
        events.append(dict(timestamp=t.isoformat(),event='UNDERLYING',
                           contract_id='',bid=f'{spot-.01:.4f}',ask=f'{spot+.01:.4f}'))
        for j,c in enumerate(cs):
            tau=annual_time(t,c.expiry)
            iv=.22+.045*math.log(c.strike/spot)**2-.06*math.log(c.strike/spot)+.025*math.sqrt(tau)
            fair=american_price(spot,c.strike,tau,iv,.04,.005,c.right,32)
            if fair<.08: continue
            half=.08+.01*j/len(cs)
            bid=max(.01,round(fair-half,2));ask=round(max(bid+.01,fair+half),2)
            qt=t+timedelta(milliseconds=50+j*3)
            events.append(dict(timestamp=qt.isoformat(),event='QUOTE',contract_id=c.id,
                               bid=bid,ask=ask,bid_size=6,ask_size=6))
            if rng.random()<.55:
                # Customers may act on future information; maker does not.
                advantage=(path[i+1]-spot)*(1 if c.right=='C' else -1)
                prob=.5+.35*math.tanh(advantage*6)
                side='BUY' if rng.random()<prob else 'SELL'
                price=ask if side=='BUY' else bid
                tt=t+timedelta(milliseconds=150+j*3)
                events.append(dict(timestamp=tt.isoformat(),event='TRADE',
                                   contract_id=c.id,price=price,quantity=rng.choice((1,2,4,7)),
                                   aggressor=side))
    events.sort(key=lambda v:dtparse(v['timestamp']))
    return cs,events


def main():
    p=argparse.ArgumentParser(description='Options desk V7 research replay; NEVER routes real orders')
    p.add_argument('--demo',action='store_true',help='Run built-in synthetic market event replay')
    p.add_argument('--steps',type=int,default=42)
    p.add_argument('--contracts',help='Real option reference data CSV')
    p.add_argument('--events',help='Real event sequence CSV (quotes + prints)')
    p.add_argument('--output',default='desk_options_output')
    a=p.parse_args()
    if a.contracts or a.events:
        if not a.contracts or not a.events:
            p.error('Provide BOTH --contracts and --events')
        cs=read_contracts(a.contracts);rows=read_events(a.events)
    else:
        cs,rows=demo(steps=a.steps)
    engine=OptionsDesk(cs).run(rows)
    engine.export(a.output)
    print(json.dumps(engine.report(),indent=2))
    print('Output files in',Path(a.output).resolve())
    print('RESEARCH ONLY: Quote prints, venue queue and hedging are approximated.')


if __name__=='__main__':
    main()