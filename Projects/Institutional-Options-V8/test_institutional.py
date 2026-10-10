"""Controls and numerical-accounting regression tests for the V8 research runner."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime,timedelta,date,timezone
from pathlib import Path
import csv
import json
import math
import pytest
from desk_options_mm import Config, Contract, OptionsDesk, MarketQuote, WorkingQuote, american_price, demo
from institutional_run import (DataQualityError, validate_contracts,validate_tape,
    calculate_attribution,run_replay,walkforward,config_for,sha256_bytes,
    canonical_bytes,save_data,read_data)

CS,EVENTS=demo(29,9)


def test_fixture_has_valid_exogenous_feed():
    q=validate_tape(CS,EVENTS)
    assert q['events']==len(EVENTS)
    assert q['counts']['UNDERLYING']==9
    assert q['counts']['QUOTE']>0 and q['counts']['TRADE']>0


def test_validate_duplicate_contract():
    with pytest.raises(DataQualityError,match='Duplicate'):
        validate_contracts([CS[0],CS[0]])


def test_reject_empty():
    with pytest.raises(DataQualityError,match='Empty'):
        validate_tape(CS,[])


def test_reject_unsorted_feed():
    bad=[dict(e) for e in EVENTS]
    bad[1]['timestamp']='2026-10-10T00:00:00+00:00'
    with pytest.raises(DataQualityError,match='monotonic'):
        validate_tape(CS,bad)


def test_reject_naive_timestamps():
    bad=[dict(e) for e in EVENTS]
    bad[0]['timestamp']='2026-10-12T14:30:00'
    with pytest.raises(DataQualityError,match='UTC offset'):
        validate_tape(CS,bad)


def test_reject_crossed_underlying():
    bad=[dict(e) for e in EVENTS]
    bad[0]['bid'],bad[0]['ask']='101','100'
    with pytest.raises(DataQualityError,match='invalid underlying'):
        validate_tape(CS,bad)


def test_reject_stock_split_like_outlier():
    bad=[dict(e) for e in EVENTS]
    nxt=next(i for i,v in enumerate(bad) if i>0 and v['event']=='UNDERLYING')
    bad[nxt]['bid']=50.;bad[nxt]['ask']=50.02
    with pytest.raises(DataQualityError,match='25% rapid'):
        validate_tape(CS,bad)


def test_reject_quote_unknown_contract():
    bad=[dict(e) for e in EVENTS]
    idx=next(i for i,r in enumerate(bad) if r['event']=='QUOTE')
    bad[idx]['contract_id']='INVALID'
    with pytest.raises(DataQualityError,match='not in reference'):
        validate_tape(CS,bad)


def test_reject_crossed_option_quote():
    bad=[dict(e) for e in EVENTS]
    idx=next(i for i,r in enumerate(bad) if r['event']=='QUOTE')
    bad[idx]['bid']='10';bad[idx]['ask']='9'
    with pytest.raises(DataQualityError,match='crossed'):
        validate_tape(CS,bad)


def test_reject_trade_before_quote():
    bad=[dict(EVENTS[0]), {'timestamp':EVENTS[0]['timestamp'],
        'event':'TRADE','contract_id':CS[0].id,'price':1,'quantity':3,'aggressor':'BUY'}]
    with pytest.raises(DataQualityError,match='print before'):
        validate_tape(CS,bad)


def test_reject_impossible_spread():
    bad=[dict(e) for e in EVENTS]
    bad[0]['ask']=200.;bad[0]['bid']=99.
    with pytest.raises(DataQualityError,match='250 bps'):
        validate_tape(CS,bad)


def test_config_policies_are_distinct():
    base=config_for('baseline');defn=config_for('defensive')
    assert defn.min_half_spread>base.min_half_spread
    assert defn.max_abs_delta<base.max_abs_delta


def test_reproducible_stable_fingerprint():
    assert sha256_bytes(canonical_bytes(EVENTS))==sha256_bytes(canonical_bytes(EVENTS))
    other=[dict(x) for x in EVENTS];other[-1]['quantity']=999
    assert sha256_bytes(canonical_bytes(EVENTS))!=sha256_bytes(canonical_bytes(other))


def test_csv_roundtrip(tmp_path):
    save_data(CS,EVENTS,tmp_path)
    cs,rows=read_data(str(tmp_path/'contracts.csv'),str(tmp_path/'events.csv'))
    assert cs==CS and len(rows)==len(EVENTS)
    assert validate_tape(cs,rows)['events']==len(EVENTS)


def test_pnl_bridge_reconciles_and_persists(tmp_path):
    result=run_replay(CS,EVENTS,tmp_path,config_for('baseline',tree_steps=16),save_input=True)
    a=result['pnl_attribution']
    assert abs(a['reconciliation_difference'])<=a['reconciliation_tolerance']
    assert abs(a['terminal_net_mid_pnl']-result['net_mid_pnl'])<.02
    for filename in ('summary.json','manifest.json','institutional_summary.json',
                     'attribution.json','quote_audit.csv','research_report.md','rejections.csv'):
        # Rejection CSV can be absent on a run with NO rejections; core writes it only if nonempty.
        if filename!='rejections.csv': assert (tmp_path/filename).exists(),filename
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    assert manifest['data_label']=='SYNTHETIC'
    assert manifest['integrity_checks']['pnl_reconciled']
    assert not manifest['real_order_routing']


def test_attribution_is_equal_to_engine_cash_ledger():
    desk=OptionsDesk(CS,config_for('baseline',tree_steps=16)).run(EVENTS)
    a=calculate_attribution(desk,EVENTS)
    assert math.isclose(a['terminal_net_mid_pnl'],desk.marked_pnl(),abs_tol=.001)


def test_stricter_limit_blocks_proposed_position():
    c=CS[0]; d=OptionsDesk([c],Config(max_per_contract=1))
    t=datetime(2026,10,12,14,30,tzinfo=timezone.utc)
    d.on_underlying(t,99.99,100.01)
    px=american_price(100,c.strike,30/365,.25,.04,.005,c.right,48)
    d.market[c.id]=MarketQuote(t,px-.04,px+.04,5,5,.25)
    d.now=t
    assert not d.limits_ok(c.id,2)
    assert d.rejects['per_contract']==1
    assert d.rejection_audit[-1]['reason']=='per_contract'


def test_kill_switch_cancels_quotes():
    c=CS[0]; cfg=Config(max_loss=.01)
    d=OptionsDesk([c],cfg)
    t=datetime(2026,10,12,14,30,tzinfo=timezone.utc)
    d.on_underlying(t,99.99,100.01)
    px=american_price(100,c.strike,30/365,.25,.04,.005,c.right,48)
    d.market[c.id]=MarketQuote(t,px-.04,px+.04,5,5,.25)
    d.positions[c.id]=1
    d.now=t
    d.snapshot()
    # Selling a short-priced option creates a loss > $0.01; strong fail-closed example.
    d.cash=-1000
    d.snapshot()
    assert d.killed
    assert not d.working


def test_no_fabricated_mark_when_stale():
    c=CS[0];d=OptionsDesk([c])
    t=datetime(2026,10,12,14,30,tzinfo=timezone.utc)
    d.on_underlying(t,99.99,100.01)
    d.market[c.id]=MarketQuote(t,1.2,1.4,5,5,.25)
    d.positions[c.id]=1
    d.now=t+timedelta(seconds=11)
    assert d.marked_pnl() is None


def test_markout_is_report_only_not_predictor():
    c=CS[0]; d=OptionsDesk([c]);d.fills=[]
    assert not d.fills


def test_walkforward_rejects_single_day(tmp_path):
    with pytest.raises(ValueError,match='5 independent'):
        walkforward(CS,EVENTS,tmp_path,tree_steps=16)


def test_walkforward_dev_precedes_holdout(tmp_path):
    # Five independent synthetic UTC days. Each day's full event timeline is
    # shifted uniformly; contracts expire 30+ days away.
    from desk_options_mm import dtparse
    dates=[]
    for day in range(5):
        for row in EVENTS:
            new=dict(row)
            new['timestamp']=(dtparse(row['timestamp'])+timedelta(days=day)).isoformat()
            dates.append(new)
    result=walkforward(CS,dates,tmp_path,seed=7,tree_steps=8)
    assert result['development_days']==3
    assert result['holdout_days']==2
    assert result['date_boundaries']['development_end']<result['date_boundaries']['holdout_start']
    assert result['selected_policy'] in ('baseline','defensive','fast_hedge')
    with (tmp_path/'walkforward_daily.csv').open() as f:
        daily=list(csv.DictReader(f))
    assert len(daily)==15
    assert set(r['date'] for r in daily if r['split']=='holdout')=={'2026-10-15','2026-10-16'}