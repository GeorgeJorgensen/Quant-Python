"""V8 institutional-style OFFLINE RESEARCH control plane.

No broker endpoints, API keys, order routing, or real orders exist.

Dependencies: numpy + desk_options_mm.py (included locally).
Architecture: strict CSV preflight -> immutable replay -> trade/cash reconciliation
              -> exact additive P&L bridge -> risk & decision audit -> manifest.

All valuations assume one underlying and single, non-expiring observation
window, with regular American equity options, continuous dividend yield and
synthetic latency/queue approximations. Production trading requires much more.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, date, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable

from desk_options_mm import Contract, Config, OptionsDesk, demo, dtparse, annual_time

SCHEMA = ('timestamp','event','contract_id','bid','ask','bid_size',
          'ask_size','price','quantity','aggressor')
CONTRACT_SCHEMA = ('id','expiry','strike','right','multiplier')
SOFTWARE_VERSION = '8.0.0-research'

class DataQualityError(ValueError):
    """Abort the entire run instead of cherry-picking convenient records."""


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canonical_bytes(rows: Iterable[dict]) -> bytes:
    """Stable serialization: the same tape and config produce the same run hash."""
    return json.dumps(list(rows),sort_keys=True,separators=(',',':'),default=str).encode()


def validate_contracts(contracts: list[Contract]) -> dict:
    if not contracts:
        raise DataQualityError('No contracts provided')
    seen=set()
    for c in contracts:
        if c.id in seen:
            raise DataQualityError(f'Duplicate contract id: {c.id}')
        seen.add(c.id)
        if not (math.isfinite(c.strike) and c.strike>0):
            raise DataQualityError('Invalid strike')
        if c.multiplier<1 or c.right not in ('C','P'):
            raise DataQualityError('Invalid contract reference data')
    return {'count':len(contracts),'earliest_expiry':min(c.expiry for c in contracts).isoformat(),
            'last_expiry':max(c.expiry for c in contracts).isoformat()}


def validate_tape(contracts: list[Contract], tape: list[dict]) -> dict:
    """Fail-closed causal feed QC; NEVER reorder corrupted historical events.

    Stable same-timestamp feed events are supported, but the first must be
    UNDERLYING. A print must reference an already seen option quote. We do not
    assume a consolidated print establishes that the simulated order was filled.
    """
    allowed={c.id:c for c in contracts}
    if not tape:
        raise DataQualityError('Empty feed')
    if len(tape)>2_000_000:
        raise DataQualityError('Too many events for the local research runner')
    seen_underlying=False
    known_quotes=set()
    counts=Counter()
    previous=None
    last_spot=None
    last_underlying=None
    first=None
    for i,row in enumerate(tape,1):
        try:
            t=dtparse(str(row['timestamp']))
        except (ValueError,KeyError,TypeError) as e:
            raise DataQualityError(f'Row {i}: timestamp must be ISO-8601 with UTC offset: {e}') from e
        if previous is not None and t<previous:
            raise DataQualityError(f'Row {i}: timestamps not monotonic; refuse to sort')
        previous=t
        first=first or t
        kind=row.get('event')
        if kind not in ('UNDERLYING','QUOTE','TRADE'):
            raise DataQualityError(f'Row {i}: unknown event {kind!r}')
        counts[kind]+=1
        if kind=='UNDERLYING':
            try: bid,ask=float(row['bid']),float(row['ask'])
            except (ValueError,TypeError,KeyError) as e: raise DataQualityError(f'Row {i}: bad underlying') from e
            if not (math.isfinite(bid) and math.isfinite(ask) and 0<bid<ask):
                raise DataQualityError(f'Row {i}: invalid underlying NBBO')
            if (ask-bid)/((ask+bid)/2)>0.025:
                raise DataQualityError(f'Row {i}: stock spread exceeds 250 bps sanity threshold')
            midpoint=(bid+ask)/2
            if last_spot is not None and last_underlying and (t-last_underlying).total_seconds()<3600:
                if abs(midpoint/last_spot-1)>.25:
                    raise DataQualityError(f'Row {i}: >25% rapid stock move; investigate split/bad tick')
            last_spot,last_underlying=midpoint,t
            seen_underlying=True
        else:
            cid=row.get('contract_id')
            if cid not in allowed:
                raise DataQualityError(f'Row {i}: option contract not in reference data: {cid}')
            if not seen_underlying:
                raise DataQualityError(f'Row {i}: option event preceding underlying mark')
            c=allowed[cid]
            if annual_time(t,c.expiry)<=0:
                raise DataQualityError(f'Row {i}: expired option; corporate actions/exercise unsupported')
            if kind=='QUOTE':
                try:
                    bid,ask=float(row['bid']),float(row['ask'])
                    bids,asks=int(row['bid_size']),int(row['ask_size'])
                except (ValueError,TypeError,KeyError) as e:
                    raise DataQualityError(f'Row {i}: bad option quote') from e
                if not (all(map(math.isfinite,(bid,ask))) and 0<bid<ask and bids>0 and asks>0):
                    raise DataQualityError(f'Row {i}: crossed/invalid option quote')
                if (ask-bid)/max(.01,(ask+bid)/2)>5:
                    raise DataQualityError(f'Row {i}: option spread exceeds sanity limit')
                known_quotes.add(cid)
            else:
                if cid not in known_quotes:
                    raise DataQualityError(f'Row {i}: print before any quote for contract')
                try:
                    px=float(row['price']); size=int(row['quantity'])
                except (ValueError,TypeError,KeyError) as e:
                    raise DataQualityError(f'Row {i}: invalid trade') from e
                if not math.isfinite(px) or px<=0 or size<=0 or row.get('aggressor') not in ('BUY','SELL'):
                    raise DataQualityError(f'Row {i}: invalid trade execution')
    if not counts['QUOTE'] or not counts['UNDERLYING']:
        raise DataQualityError('Missing underlying or option quotes')
    return {'events':len(tape),'counts':dict(counts),'first_timestamp':first.isoformat(),
            'last_timestamp':previous.isoformat(),'contract_quotes_covered':len(known_quotes),
            'scope':'ONE underlying, time ordered, offline',
            'quality':'Passed strict structural checks; NOT independently validated exchange feed'}


def read_data(contracts_csv: str,events_csv: str):
    with open(contracts_csv,newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if not set(CONTRACT_SCHEMA).issubset(reader.fieldnames or []):
            raise DataQualityError('contracts CSV fields: '+','.join(CONTRACT_SCHEMA))
        cs=[Contract(r['id'],date.fromisoformat(r['expiry']),float(r['strike']),
                     r['right'],int(r['multiplier'])) for r in reader]
    with open(events_csv,newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        if not set(SCHEMA).issubset(reader.fieldnames or []):
            raise DataQualityError('events CSV fields: '+','.join(SCHEMA))
        rows=[dict(r) for r in reader]
    return cs,rows


def save_data(cs,tape,folder:Path):
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'contracts.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=CONTRACT_SCHEMA);w.writeheader()
        for c in cs: w.writerow({'id':c.id,'expiry':c.expiry.isoformat(),
                       'strike':c.strike,'right':c.right,'multiplier':c.multiplier})
    with (folder/'events.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=SCHEMA);w.writeheader()
        for row in tape: w.writerow({k:row.get(k,'') for k in SCHEMA})


def calculate_attribution(desk:OptionsDesk,tape:list[dict]):
    """Exact trade-based P&L decomposition under mid marks (NO attribution fantasy).

    Identity, assuming zero opening inventory and cash:
      terminal P&L = captured spread-at-fill + subsequent option mark movement
                   + stock hedge execution cost + subsequent stock mark movement
                   - option commissions - stock commissions.

    This is accounting decomposition, NOT causal explanation of alpha. The
    signed stock execution cost includes simulated bid/ask and slippage.
    """
    if desk.now is None or desk.underlying_bid is None:
        raise ValueError('Underlying price unavailable')
    latest_opt={cid:(q.bid+q.ask)/2 for cid,q in desk.market.items()}
    terminal_stock=(desk.underlying_bid+desk.underlying_ask)/2
    mids_by_hedge_timestamp={r['timestamp']:(float(r['bid'])+float(r['ask']))/2
            for r in tape if r['event']=='UNDERLYING'}
    spread=option_drift=0.
    for f in desk.fills:
        if f.contract not in latest_opt:
            raise ValueError('Missing terminal option mark for executed contract')
        signed=f.quantity if f.maker_side=='BUY' else -f.quantity
        multiplier=desk.contracts[f.contract].multiplier
        spread+=signed*(f.observed_mid-f.price)*multiplier
        option_drift+=signed*(latest_opt[f.contract]-f.observed_mid)*multiplier
    stock_execution=stock_drift=0.
    for h in desk.hedges:
        if h['time'] not in mids_by_hedge_timestamp:
            raise ValueError('No underlying event corresponding to stock hedge')
        qty=int(h['shares'])
        mid=mids_by_hedge_timestamp[h['time']]
        stock_execution+=qty*(mid-float(h['price']))
        stock_drift+=qty*(terminal_stock-mid)
    fees_options=sum(f.quantity*desk.cfg.option_fee for f in desk.fills)
    fees_stocks=sum(float(h['fees']) for h in desk.hedges)
    # Hedge logs round stock prices to 5 decimals; expect small drift from
    # unrounded cash ledger. Verify within 1/2 tick for each hedge share.
    components={'option_spread_at_fill':round(spread,6),
                'option_post_fill_mark_movement':round(option_drift,6),
                'stock_hedge_execution_vs_mid':round(stock_execution,6),
                'stock_post_hedge_mark_movement':round(stock_drift,6),
                'option_fees':round(-fees_options,6),
                'stock_fees':round(-fees_stocks,6)}
    decomposition=sum(components.values())
    by_positions=desk.cash+desk.stock_shares*terminal_stock
    for cid,qty in desk.positions.items():
        if qty:
            if cid not in latest_opt: raise ValueError('Missing mark')
            by_positions+=qty*desk.contracts[cid].multiplier*latest_opt[cid]
    by_engine=desk.marked_pnl()
    if by_engine is None:
        raise RuntimeError('Stale or incomplete final mark: refuse financial reporting')
    error=decomposition-by_positions
    if not math.isclose(by_engine,by_positions,abs_tol=1e-5,rel_tol=1e-10):
        raise RuntimeError('Accounting mismatch between ledger and desk state')
    tolerance=0.000005*sum(abs(h['shares']) for h in desk.hedges)+0.0001
    if abs(error)>tolerance:
        raise RuntimeError(f'P&L attribution does not reconcile: {error:.6f} > {tolerance:.6f}')
    return {'currency':'USD','terminal_net_mid_pnl':round(by_engine,4),
            'components':components,'components_sum':round(decomposition,4),
            'reconciliation_difference':round(error,6),
            'reconciliation_tolerance':round(tolerance,6),
            'note':'Trade-to-terminal-mid accounting bridge, not realized-alpha attribution'}


def config_for(name:str,seed:int=29,tree_steps:int=32):
    base=Config(seed=seed,tree_steps=tree_steps)
    if name=='baseline':return base
    if name=='defensive':return replace(base,min_half_spread=.055,
        model_uncertainty_pct=.022,inventory_skew_dollars=.14,
        max_abs_delta=480.,max_abs_vega=24_000.)
    if name=='fast_hedge':return replace(base,hedge_trigger=45.,max_hedge_per_event=160,
        stock_slippage_bps=2.)
    raise ValueError('Unknown policy; select baseline, defensive, fast_hedge')


def run_replay(cs:list[Contract],rows:list[dict],out:Path,
               cfg:Config,source_label='SYNTHETIC',save_input=False) -> dict:
    # Strict structural QC occurs BEFORE trading or writing performance.
    quality=validate_contracts(cs)
    tape_qc=validate_tape(cs,rows)
    fingerprint=sha256_bytes(canonical_bytes([{'contracts':[asdict(c) for c in cs],
                                               'cfg':asdict(cfg)},*rows]))
    engine=OptionsDesk(cs,cfg).run(rows)
    if not engine.risk_history:
        raise RuntimeError('No valid complete risk snapshot; abort')
    attribution=calculate_attribution(engine,rows)
    result=engine.report()
    result['pnl_attribution']=attribution
    if abs(result['net_mid_pnl']-attribution['terminal_net_mid_pnl'])>.011:
        raise RuntimeError('Summary P&L disagrees with ledger')
    out.mkdir(parents=True,exist_ok=True)
    engine.export(out)
    if save_input: save_data(cs,rows,out/'tape')
    (out/'attribution.json').write_text(json.dumps(attribution,indent=2))
    provenance={'version':SOFTWARE_VERSION,'data_label':source_label,
        'independently_verified_market_data':False,
        'actual_exchange_queue_reconstruction':False,
        'real_order_routing':False,
        'fingerprint_sha256':fingerprint,
        'input_event_sha256':sha256_bytes(canonical_bytes(rows)),
        'contract_reference':quality,'feed_preflight':tape_qc,
        'configuration':asdict(cfg),
        'integrity_checks':{'pnl_reconciled':True,'chronological':True,
         'strict_schema':True,'fail_closed_stale_marks':True},
        'limitations':['No exchange-native order-book or venue routing',
           'No historical borrow/dividend calendars or assignment/expiry processing',
           'Trade capture and queue behavior require empirical calibration',
           'Single underlying and no end-to-end market-access controls']}
    (out/'manifest.json').write_text(json.dumps(provenance,indent=2,default=str))
    result['reproducibility_hash']=fingerprint
    result['data_label']=source_label
    (out/'institutional_summary.json').write_text(json.dumps(result,indent=2))
    # Human-reviewable risk memo: metrics, reconciliation, and provenance in
    # one place. Numbers are rounded for display, not for the control checks.
    components=attribution['components']
    lines=[f'# Options Market-Making V8 — Offline Risk Report',
           '',f'**Source:** {source_label} (unverified for authenticity)',
           f'**Run SHA-256:** `{fingerprint}`','',
           '## Executive metrics',
           f'- Events: {result["events"]:,}; option fills: {result["fills"]:,}; stock hedges: {result["stock_hedges"]:,}',
           f'- Net midpoint P&L: ${result["net_mid_pnl"]:,.2f}',
           f'- Estimated liquidation P&L: ${result["liquidation_pnl"]:,.2f}',
           f'- Max sampled drawdown (dollars): ${result["max_observed_drawdown"]:,.2f}',
           f'- Portfolio kill switch activated: {result["kill_switch"]}',
           '', '## Accounting bridge (USD)',
           '| Component | P&L |','|---|---:|',
           *[f'| {k.replace("_"," ")} | ${v:,.2f} |' for k,v in components.items()],
           f'| **Components total** | **${attribution["components_sum"]:,.2f}** |',
           f'| **Engine marked P&L** | **${attribution["terminal_net_mid_pnl"]:,.2f}** |',
           f'| Residual (rounding) | ${attribution["reconciliation_difference"]:,.6f} |',
           '', '## Ending portfolio risks',
           *[f'- {k}: {v:,.2f}' for k,v in result['exposures'].items()],
           '', '## Rejections',
           *(f'- {k}: {v}' for k,v in sorted(result['rejection_counts'].items())),
           '', '## Disclosure',
           'Offline simulation only. No independent exchange feed verification, broker',
           'order routing, actual queue reconstruction, exercise/assignment processing,',
           'or regulatory compliance certification. Numerical reconciliation is not',
           'proof of model correctness, execution feasibility, or trading profitability.']
    (out/'research_report.md').write_text('\n'.join(lines)+'\n')
    return result


def walkforward(cs:list[Contract],rows:list[dict],out:Path,seed:int=29,
                tree_steps:int=16,development_fraction:float=.6):
    """Strict chronological development/holdout, with separate daily books.

    Caveat: this is daily-flat, resetting inventory at every day boundary.
    Strategies are selected ONCE from past development days. Holdout results
    never affect the chosen policy. Input is NOT fitted; synthetic feed cannot
    establish predictive ability. For trading research use many independent days.
    """
    validate_contracts(cs);validate_tape(cs,rows)
    if not 0.4<=development_fraction<=0.8:
        raise ValueError('Development fraction must be 0.4..0.8')
    by_day={}
    for r in rows:
        d=dtparse(r['timestamp']).date().isoformat()
        by_day.setdefault(d,[]).append(r)
    days=sorted(by_day)
    if len(days)<5:raise ValueError('Need at least 5 independent UTC feed-days for walk-forward')
    cut=max(2,min(len(days)-2,math.floor(len(days)*development_fraction)))
    # For each day, require first event underlying. Daily positions reset.
    for day in days:
        validate_tape(cs,by_day[day])
    policies=['baseline','defensive','fast_hedge']
    daily=[]
    for day in days:
        for policy in policies:
            cfg=config_for(policy,seed,tree_steps)
            desk=OptionsDesk(cs,cfg).run(by_day[day])
            if not desk.risk_history or desk.marked_pnl(liquidation=True) is None:
                raise RuntimeError(f'Day {day} lacks valid complete final marks')
            attribution=calculate_attribution(desk,by_day[day])
            daily.append({'date':day,'split':'development' if day<days[cut] else 'holdout',
                'strategy':policy,'liquidation_pnl':desk.marked_pnl(liquidation=True),
                'mid_pnl':attribution['terminal_net_mid_pnl'],
                'fills':len(desk.fills),'hedges':len(desk.hedges)})
    # Select ONLY using earliest dates; no holdout statistics read here.
    dev={policy:mean(r['liquidation_pnl'] for r in daily if r['split']=='development' and r['strategy']==policy)
         for policy in policies}
    selected=max(policies,key=lambda policy:dev[policy])
    holdout=[r for r in daily if r['split']=='holdout' and r['strategy']==selected]
    result={'mode':'CHRONOLOGICAL DAILY-FLAT OUT-OF-SAMPLE REPLAY',
            'selection_metric':'mean daily liquidation P&L',
            'date_boundaries':{'development_end':days[cut-1], 'holdout_start':days[cut]},
            'development_days':cut,'holdout_days':len(days)-cut,
            'development_mean_by_policy':dev,
            'selected_policy':selected,
            'holdout_mean_daily_liquidation_pnl':mean(x['liquidation_pnl'] for x in holdout),
            'holdout_total_liquidation_pnl':sum(x['liquidation_pnl'] for x in holdout),
            'note':'Daily reset; selection based only on development dates; requires real tape for empirical validity'}
    out.mkdir(parents=True,exist_ok=True)
    with (out/'walkforward_daily.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(daily[0]));w.writeheader();w.writerows(daily)
    (out/'walkforward_summary.json').write_text(json.dumps(result,indent=2))
    return result


def main():
    ap=argparse.ArgumentParser(description='Institutional-style OFFLINE options research, NO broker routing')
    ap.add_argument('--contracts',type=str,help='Reference CSV; with --events')
    ap.add_argument('--events',type=str,help='UTC-offset timestamped feed CSV; with --contracts')
    ap.add_argument('--demo',action='store_true',help='Synthetic fixture, never authentic market data')
    ap.add_argument('--steps',type=int,default=30)
    ap.add_argument('--seed',type=int,default=29)
    ap.add_argument('--tree-steps',type=int,default=32)
    ap.add_argument('--policy',choices=('baseline','defensive','fast_hedge'),default='baseline')
    ap.add_argument('--walkforward',action='store_true',help='Requires >=5 independent feed dates')
    ap.add_argument('--output',default='results_v8')
    a=ap.parse_args()
    if a.tree_steps<8 or a.steps<4:ap.error('Require --tree-steps >=8 and --steps >=4')
    if bool(a.contracts)!=bool(a.events):ap.error('Provide both --contracts and --events')
    if not a.contracts and not a.demo:ap.error('Provide --demo OR --contracts and --events; no implicit synthetic performance')
    cs,rows=read_data(a.contracts,a.events) if a.contracts else demo(a.seed,a.steps)
    if a.walkforward:
        if not a.contracts:ap.error('Walk-forward needs an explicit dated feed')
        report=walkforward(cs,rows,Path(a.output),a.seed,a.tree_steps)
    else:
        report=run_replay(cs,rows,Path(a.output),config_for(a.policy,a.seed,a.tree_steps),
                          'USER_PROVIDED_UNVERIFIED_FEED' if a.contracts else 'SYNTHETIC',True)
    print(json.dumps(report,indent=2,default=str))
    print('Outputs:',Path(a.output).resolve())
    print('RESEARCH ONLY. NOT LICENSED EXCHANGE DATA, EXECUTION OR PRODUCTION COMPLIANCE.')

if __name__=='__main__':main()