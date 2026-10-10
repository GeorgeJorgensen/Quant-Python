"""MONTE CARLO RESEARCH HARNESS — VERSION 7

Educational options market-making research, NOT a production trading engine.
Run from the folder containing desk_options_mm_v7.py:

    python monte_carlo.py --paths 100 --steps 32 --output monte_carlo_results
    python monte_carlo.py --paths 60 --compare defensive --output paired_comparison
    python monte_carlo.py --paths 8 --steps 12 --output smoke_test

CAUSALITY: the generator knows the NEXT move, modeling informed customer flow.
The trading desk receives only chronologically ordered current-time events.
No next price, jump flag, or regime label is passed into the desk strategy.

CAVEAT: The underlying market, option NBBO, trades, and liquidity are simulated.
No backtest here establishes real alpha or Citadel-grade execution feasibility.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from desk_options_mm_v7 import (
    Config, Contract, OptionsDesk, american_price, annual_time, dtparse
)

# Trading-year fraction for a single 5-minute US equities interval.
# Annualized vol of 20% => ~0.14% return standard deviation per 5-min bar.
TRADING_BARS_PER_YEAR = 252 * 78
PER_BAR_YEARS = 1.0 / TRADING_BARS_PER_YEAR


@dataclass(frozen=True)
class Regime:
    """Explicit scenario assumptions. Parameters are NOT estimated from data.

    drift_per_bar_bp: Bar-specific drift, in basis points, not annual drift.
    sigma_annual: Annualized spot volatility target.
    mean_revert: Return speed of log-volatility toward the target each bar.
    vol_of_vol_bar: Standard deviation of log(sigma) shock each bar.
    shock: A one-time jump at a random step, used to guarantee stress coverage.
    informed: Probability customer direction depends on next information.
    """
    name: str
    sigma_annual: float
    drift_per_bar_bp: float
    vol_of_vol_bar: float
    jump_probability: float
    jump_mean: float
    jump_std: float
    shock: float = 0.0
    informed: float = 0.60
    spread_multiplier: float = 1.0
    trade_probability: float = 0.55
    size_multiplier: float = 1.0
    mean_revert: float = 0.10


REGIMES = (
    Regime('calm', .14, 0.0, .027, .002, 0.00, .003,
           informed=.40, spread_multiplier=.8, trade_probability=.40),
    Regime('normal', .25, .10, .045, .004, -0.004, .01,
           informed=.58, spread_multiplier=1.0),
    Regime('trend_up', .27, 2.5, .052, .005, .006, .010,
           informed=.70, spread_multiplier=1.05),
    Regime('trend_down', .29, -2.5, .052, .005, -.006, .010,
           informed=.70, spread_multiplier=1.10),
    Regime('crash', .50, -.6, .10, .023, -.026, .016,
           shock=-.045, informed=.82, spread_multiplier=2.0,
           trade_probability=.78, size_multiplier=1.75),
    Regime('squeeze', .40, .6, .08, .018, .025, .014,
           shock=.040, informed=.78, spread_multiplier=1.6,
           trade_probability=.72, size_multiplier=1.4),
)


def instruments(start: date, expiries: int = 1) -> list[Contract]:
    """One or two maturity slices with 95/100/105 call/put pairs.

    Static strikes deliberately avoid changing instrument IDs through a run.
    For an actual listed chain, identifiers, expirations and adjustments must
    come from a licensed option reference feed, not this constructor.
    """
    if expiries not in (1, 2):
        raise ValueError('expiries must equal 1 or 2')
    tenors = (30, 75)[:expiries]
    return [Contract(f'SYN_{days}_{k}_{right}',start+timedelta(days=days),
                     float(k),right) for days in tenors
            for k in (95, 100, 105) for right in ('C', 'P')]


def make_tape(seed: int, steps: int, regime: Regime,
              expiries: int = 1) -> tuple[list[Contract], list[dict], dict]:
    """Generate the market FIRST, then replay it identically per strategy.

    Spot: discretized lognormal diffusion with mean-reverting STOCHASTIC VOL,
    negative return/vol innovation correlation, and compound-Poisson jumps.
    Options: same underlying, smile + term structure, American binomial tree,
    liquidity-sensitive synthetic NBBO and hypothetical aggressor prints.

    The ONE-TIME shock for 'crash'/'squeeze' happens mid-path, making those
    regimes meaningful in small samples. No genuine option-chain feed used.
    """
    if steps < 4:
        raise ValueError('steps must be at least 4')
    rng = random.Random(seed)
    start = datetime(2026, 10, 12, 14, 30, tzinfo=timezone.utc)
    contracts = instruments(start.date(), expiries)
    spot = [100.0]
    sigmas = [regime.sigma_annual]
    jumps = 0
    shock_step = rng.randrange(max(2, steps // 3), max(3, 2*steps // 3))
    for i in range(steps):
        # Correlation -0.60: downward spot surprises raise volatility more.
        z_spot = rng.gauss(0,1)
        z_ind = rng.gauss(0,1)
        z_vol = -.60*z_spot + math.sqrt(1-.60**2)*z_ind
        logv = (math.log(sigmas[-1])
                + regime.mean_revert*(math.log(regime.sigma_annual)-math.log(sigmas[-1]))
                + regime.vol_of_vol_bar*z_vol)
        new_sigma = max(.07, min(1.50, math.exp(logv)))
        jump = 0.0
        if rng.random() < regime.jump_probability:
            jump += rng.gauss(regime.jump_mean,regime.jump_std)
            jumps += 1
        if i == shock_step and regime.shock:
            jump += regime.shock * rng.uniform(.7,1.3)
            jumps += 1
            new_sigma = min(1.5,new_sigma*(1.25 if regime.shock < 0 else 1.1))
        # Log-return drift per bar explicitly measured in basis points.
        log_return = (regime.drift_per_bar_bp/10_000
                      - .5*new_sigma**2*PER_BAR_YEARS
                      + new_sigma*math.sqrt(PER_BAR_YEARS)*z_spot+ jump)
        spot.append(spot[-1]*math.exp(log_return))
        sigmas.append(new_sigma)

    events: list[dict] = []
    for i in range(steps):
        t = start + timedelta(minutes=5*i)
        s = spot[i]
        vol = sigmas[i]
        # Underlying spreads widen during stress; neither the desk nor its
        # hedge simulator can execute at an imaginary frictionless midpoint.
        half_stock = max(.005, round(.01*regime.spread_multiplier, 4))
        events.append(dict(timestamp=t.isoformat(),event='UNDERLYING',
                           contract_id='',bid=round(s-half_stock,4),
                           ask=round(s+half_stock,4)))
        for j,c in enumerate(contracts):
            tau = annual_time(t,c.expiry)
            moneyness = math.log(c.strike / s)
            # IV smile is generated, and the desk must infer IV from NBBO.
            surface_iv = min(1.8,max(.07,vol + .065*moneyness*moneyness
                           - .08*moneyness + .015*math.sqrt(tau)))
            fair = american_price(s,c.strike,tau,surface_iv,.04,.005,c.right,24)
            if fair < .07:
                continue
            half = (.075 + .08*surface_iv + .012*j/len(contracts)) * regime.spread_multiplier
            bid = round(max(.01, math.floor((fair-half)*100)/100),2)
            ask = round(max(bid+.01,math.ceil((fair+half)*100)/100),2)
            qt = t+timedelta(milliseconds=50+j*4)
            book_size=max(1,round(7/regime.spread_multiplier))
            events.append(dict(timestamp=qt.isoformat(), event='QUOTE',
                               contract_id=c.id,bid=bid,ask=ask,
                               bid_size=book_size,ask_size=book_size))
            if rng.random()<regime.trade_probability:
                # IMPORTANT: informed-flow advantage uses NEXT move only in
                # the external generator, never in the trading engine.
                direction = 1 if c.right=='C' else -1
                upcoming = direction*math.log(spot[i+1]/s)*320 + (sigmas[i+1]-vol)*7
                informed_prob=.5+.42*math.tanh(upcoming)
                prob_buy = (1-regime.informed)*.5 + regime.informed*informed_prob
                aggressor='BUY' if rng.random()<prob_buy else 'SELL'
                qty_base=rng.choice((2,3,5,8,13))
                qty=max(1,round(qty_base*regime.size_multiplier))
                tt=t+timedelta(milliseconds=250+j*4)
                events.append(dict(timestamp=tt.isoformat(),event='TRADE',
                                   contract_id=c.id,price=ask if aggressor=='BUY' else bid,
                                   quantity=qty,aggressor=aggressor))
    # Tie-breaking is stable: at identical UTC times preserve source ordering.
    events.sort(key=lambda row:dtparse(row['timestamp']))
    meta=dict(seed=seed,regime=regime.name,shocks=jumps,
              start_spot=round(spot[0],4),end_spot=round(spot[-1],4),
              total_return_pct=round(100*(spot[-1]/spot[0]-1),3),
              max_sigma=round(max(sigmas),4))
    return contracts,events,meta


def variant_config(name: str, seed: int, tree_steps: int) -> Config:
    """Competing policies share an identical tape (common random numbers).

    Parameters are examples, NOT fitted; use development-set research and
    a completely untouched holdout before claiming meaningful performance.
    """
    base=Config(tree_steps=tree_steps,seed=seed)
    if name=='baseline':
        return base
    if name=='defensive':
        # Quote wider; may sacrifice fills and affect selected flow.
        return replace(base,min_half_spread=.055,
                       model_uncertainty_pct=.022,
                       inventory_skew_dollars=.14,
                       max_abs_delta=480.,max_abs_vega=24000.)
    if name=='fast_hedge':
        return replace(base,hedge_trigger=45.,max_hedge_per_event=160,
                       stock_slippage_bps=2.0)
    if name=='tight_quotes':
        return replace(base,min_half_spread=.015,
                       model_uncertainty_pct=.004)
    raise ValueError('Unknown strategy variant: '+name)


def run_one(seed: int, steps: int, regime: Regime,
            expiries: int, strategy: str, tree_steps: int,
            save_events: bool=False):
    contracts, tape, meta = make_tape(seed,steps,regime,expiries)
    desk = OptionsDesk(contracts, variant_config(strategy,seed,tree_steps))
    desk.run(tape)
    if not desk.risk_history:
        raise RuntimeError('No valid mark/risk snapshots; do not report zero P&L')
    result=desk.report()
    if result['liquidation_pnl'] is None:
        raise RuntimeError('No valid final liquidation mark')
    metrics = dict(**meta,strategy=strategy,
       pnl_mid=float(result['net_mid_pnl']),
       pnl_liquidation=float(result['liquidation_pnl']),
       max_drawdown=float(result['max_observed_drawdown']),
       first_markout=result['average_first_next_quote_markout'],
       fills=int(result['fills']),contracts_filled=int(result['option_contracts_filled']),
       stock_hedges=int(result['stock_hedges']),
       kill_switch=bool(result['kill_switch']),
       risk_rejections=sum(result['rejection_counts'].values()),
       final_delta=result['exposures']['delta'],
       final_gamma=result['exposures']['gamma'],
       final_vega=result['exposures']['vega'])
    return metrics, (desk,contracts,tape) if save_events else None


def distribution_statistics(data: Iterable[float], bootstrap_seed: int=910,
                            resamples: int=500) -> dict:
    """Use empirical quantiles/expected-shortfall; no Gaussian-P&L shortcut.

    Bootstrap confidence interval describes Monte Carlo sampling error for
    this synthetic distribution, NOT the uncertainty of market calibration.
    VaR/ES are within-scenario P&L losses from initial flat book; if the 5th
    percentile P&L is positive, loss VaR is zero by this convention.
    """
    x=np.asarray(list(data),dtype=float)
    if not len(x) or not np.isfinite(x).all():
        raise ValueError('Need finite P&L sample')
    q05=float(np.quantile(x,.05))
    q01=float(np.quantile(x,.01))
    rng=np.random.default_rng(bootstrap_seed)
    # Vectorized bootstrap of the mean; do NOT bootstrap timestamps within
    # one P&L path as though they were independent observations.
    indices=rng.integers(0,len(x),size=(resamples,len(x)))
    boot=x[indices].mean(axis=1)
    z=1.959963984540054
    wins=int(np.sum(x>0))
    p=wins/len(x)
    d=1+z*z/len(x)
    center=(p+z*z/(2*len(x)))/d
    radius=z*math.sqrt(p*(1-p)/len(x)+z*z/(4*len(x)**2))/d
    return dict(n=len(x),mean=round(float(x.mean()),2),
      median=round(float(np.median(x)),2),
      std_dev=round(float(x.std(ddof=1)),2) if len(x)>1 else None,
      win_rate=round(p,4),win_rate_wilson_95=[round(max(0.,center-radius),4),
                                                round(min(1.,center+radius),4)],
      mean_bootstrap_ci_95=[round(float(y),2) for y in np.quantile(boot,[.025,.975])],
      q05_pnl=round(q05,2),q01_pnl=round(q01,2),
      loss_var_95=round(max(0.,-q05),2),
      loss_expected_shortfall_95=round(max(0.,-float(x[x<=q05].mean())),2),
      worst=round(float(x.min()),2),best=round(float(x.max()),2))


def save_csv(path: Path, records: list[dict]):
    """Writes a header even with zero rows when supplied a schema elsewhere."""
    if not records:
        return
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(records[0]))
        w.writeheader();w.writerows(records)


def save_tape(path: Path, contracts: list[Contract],events: list[dict]):
    with (path/'worst_case_contracts.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['id','expiry','strike','right','multiplier'])
        w.writeheader()
        for c in contracts:
            w.writerow(dict(id=c.id,expiry=c.expiry.isoformat(),strike=c.strike,
                            right=c.right,multiplier=c.multiplier))
    keys=['timestamp','event','contract_id','bid','ask','bid_size',
          'ask_size','price','quantity','aggressor']
    with (path/'worst_case_events.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys)
        w.writeheader();w.writerows([{k:r.get(k,'') for k in keys} for r in events])


def maybe_plot(output: Path, records: list[dict], primary: str):
    """Charts are optional: the core research runner needs only numpy."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    subset=[r for r in records if r['strategy']==primary]
    vals=[r['pnl_liquidation'] for r in subset]
    fig, ax=plt.subplots(figsize=(9,5))
    ax.hist(vals,bins=max(5,min(30,int(math.sqrt(len(vals))*2))))
    ax.axvline(0,linestyle='--',linewidth=1)
    ax.set(title='Monte Carlo: liquidation P&L distribution',xlabel='Simulated P&L ($)',
           ylabel='Number of paths')
    fig.tight_layout();fig.savefig(output/'pnl_distribution.png',dpi=140)
    plt.close(fig)
    regimes=sorted(set(r['regime'] for r in subset))
    fig,ax=plt.subplots(figsize=(10,5))
    grouped=[[r['pnl_liquidation'] for r in subset if r['regime']==reg] for reg in regimes]
    ax.boxplot(grouped,labels=regimes,showmeans=True)
    ax.axhline(0,linestyle='--',linewidth=1)
    ax.set(title='Monte Carlo: P&L dispersion by market regime',ylabel='Liquidation P&L ($)')
    plt.xticks(rotation=25);fig.tight_layout()
    fig.savefig(output/'regime_pnl_boxplot.png',dpi=140)
    plt.close(fig)
    return True


def experiment(paths:int=60,steps:int=24,seed:int=2901,output='mc_v7_results',
               strategy='baseline',compare=None,expiries=1,tree_steps=16,
               save_worst=True,plots=True):
    """Reproducible multi-path experiment, stratified across market regimes.

    Comparing strategies uses identical exogenous tapes, which reduces noise
    in paired P&L differences. The sim still cannot reconstruct actual fills.
    Train/holdout is deterministic (3/5 vs 2/5 paths per regime). No fitted
    hyperparameters or data selection use the holdout in this implementation.
    """
    if paths<6 or steps<4 or tree_steps<8:
        raise ValueError('At least 6 paths, 4 steps, and 8 binomial steps')
    if strategy==compare:
        raise ValueError('Comparison policy must differ from primary policy')
    if compare and paths < 12:
        raise ValueError('Comparisons need at least 12 paths to cover both splits')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    records=[]
    seeds={}
    strategies=[strategy]+([compare] if compare else [])
    for path_id in range(paths):
        reg=REGIMES[path_id%len(REGIMES)]
        # 6*i and i%6 guarantee approximately balanced regime counts.
        path_seed=int(seed+104729*path_id)
        # Modulo 5 creates a prespecified, reproducible 60/40 split.
        split='development' if (path_id//len(REGIMES))%5 in (0,2,4) else 'holdout'
        seeds[path_id]=path_seed
        for name in strategies:
            result,_=run_one(path_seed,steps,reg,expiries,name,tree_steps)
            result.update(path_id=path_id,subset=split)
            records.append(result)
        if (path_id+1)%max(1,paths//10)==0:
            print(f'Completed {path_id+1}/{paths} Monte Carlo paths',flush=True)
    save_csv(output/'paths.csv',records)
    summary={
        'mode':'SYNTHETIC MONTE CARLO — NO MARKET ORDERS',
        'source':'Uncalibrated regime-dependent stochastic-vol/jump model',
        'paths':paths,'steps_per_path':steps,'seed':seed,
        'regimes': [asdict(r) for r in REGIMES],
        'main_strategy':strategy,'comparison':compare,
        'implementation_assumptions':{
           'tree_steps':tree_steps,'maturities':expiries,
           'bar_length_minutes':5,
           'scenarios_regime_balanced':True,
           'scenario_probabilities_are_not_estimated':True,
           'option_and_underlying_tape':'SYNTHETIC',
           'maker_fill_capture_uncalibrated':True,
           'CI_measures_sampling_error_not_model_error':True,
        },
        'performance':{},'regime_performance':{},'comparison_result':None,
    }
    for name in strategies:
        name_rows=[r for r in records if r['strategy']==name]
        summary['performance'][name]={
            'liquidation':distribution_statistics((r['pnl_liquidation'] for r in name_rows),seed),
            'midpoint':distribution_statistics((r['pnl_mid'] for r in name_rows),seed+1),
            'mean_hedges':round(float(np.mean([r['stock_hedges'] for r in name_rows])),2),
            'mean_fills':round(float(np.mean([r['fills'] for r in name_rows])),2),
            'mean_risk_rejections':round(float(np.mean([r['risk_rejections'] for r in name_rows])),2),
            'mean_max_drawdown_dollars':round(float(np.mean([r['max_drawdown'] for r in name_rows])),2),
            'worst_observed_drawdown_dollars':round(float(min(r['max_drawdown'] for r in name_rows)),2),
            'mean_first_next_quote_markout':round(float(np.mean([r['first_markout'] for r in name_rows if r['first_markout'] is not None])),2) if any(r['first_markout'] is not None for r in name_rows) else None,
            'kill_switch_rate':round(float(np.mean([r['kill_switch'] for r in name_rows])),4),
        }
        summary['regime_performance'][name]={reg.name:distribution_statistics(
            [r['pnl_liquidation'] for r in name_rows if r['regime']==reg.name],seed+7)
            for reg in REGIMES}
    if compare:
        lookup={(r['path_id'],r['strategy']):r for r in records}
        paired=[]
        for i in range(paths):
            main=lookup[i,strategy];other=lookup[i,compare]
            paired.append({'path_id':i,'regime':main['regime'],'subset':main['subset'],
                'primary_pnl':main['pnl_liquidation'],
                'comparison_pnl':other['pnl_liquidation'],
                'improvement_comparison_minus_primary':round(other['pnl_liquidation']-main['pnl_liquidation'],2)})
        save_csv(output/'paired_comparison.csv',paired)
        summary['comparison_result']={
            'interpretation':'Positive improvement means comparison policy outperformed primary policy.',
            'development':distribution_statistics((r['improvement_comparison_minus_primary'] for r in paired if r['subset']=='development'),seed+23),
            'holdout':distribution_statistics((r['improvement_comparison_minus_primary'] for r in paired if r['subset']=='holdout'),seed+24),
            'paired_same_market_tapes':True,
            'holdout_not_used_for_strategy_selection':True,
        }
    if save_worst:
        base=[r for r in records if r['strategy']==strategy]
        worst=min(base,key=lambda r:r['pnl_liquidation'])
        wc_reg=next(x for x in REGIMES if x.name==worst['regime'])
        _,data=run_one(seeds[worst['path_id']],steps,wc_reg,expiries,
                       strategy,tree_steps,save_events=True)
        desk,contracts,tape=data
        dest=output/'worst_case';dest.mkdir(exist_ok=True)
        save_tape(dest,contracts,tape)
        desk.export(dest)
        summary['worst_case_replay']={'path_id':worst['path_id'],
             'regime':worst['regime'],'seed':seeds[worst['path_id']],
             'folder':'worst_case', 'liquidation_pnl':worst['pnl_liquidation']}
    summary['charts_created']=maybe_plot(output,records,strategy) if plots else False
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    print('\nRESULTS:',output.resolve())
    for name in strategies:
        v=summary['performance'][name]['liquidation']
        print(f'{name}: mean ${v["mean"]:,.2f}; win rate {v["win_rate"]:.1%}; '
              f'VaR95 ${v["loss_var_95"]:,.2f}; ES95 ${v["loss_expected_shortfall_95"]:,.2f}')
    if compare:
        v=summary['comparison_result']['holdout']
        print(f'Holdout paired {compare} vs {strategy}: mean uplift ${v["mean"]:,.2f}; '
              f'95% bootstrap CI {v["mean_bootstrap_ci_95"]}')
    print('CAUTION: all data and fill rules here are synthetic and uncalibrated.')
    return summary


def main():
    p=argparse.ArgumentParser(description='V7 synthetic options market-making Monte Carlo')
    p.add_argument('--paths',type=int,default=60,help='Total paths; >=6; each explores one of six regimes')
    p.add_argument('--steps',type=int,default=24,help='5-minute bars per path; >=4')
    p.add_argument('--seed',type=int,default=2901,help='Identical seed reproduces all results')
    p.add_argument('--strategy',choices=('baseline','defensive','fast_hedge','tight_quotes'),default='baseline')
    p.add_argument('--compare',choices=('defensive','fast_hedge','tight_quotes','baseline'),default=None)
    p.add_argument('--expiries',type=int,choices=(1,2),default=1)
    p.add_argument('--tree-steps',type=int,default=16,help='American CRR steps, trade speed vs precision')
    p.add_argument('--output',default='mc_v7_results')
    p.add_argument('--no-worst-case',action='store_true')
    p.add_argument('--no-plots',action='store_true')
    a=p.parse_args()
    experiment(paths=a.paths,steps=a.steps,seed=a.seed,output=a.output,
               strategy=a.strategy,compare=a.compare,expiries=a.expiries,
               tree_steps=a.tree_steps,save_worst=not a.no_worst_case,
               plots=not a.no_plots)


if __name__=='__main__':
    main()