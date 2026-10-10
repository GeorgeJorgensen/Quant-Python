"""Run with: python -m pytest -q test_desk_options_mm.py"""
from datetime import date, datetime, timedelta, timezone
import math
import pytest
from desk_options_mm_v7 import (
    Contract, Config, MarketQuote, WorkingQuote, OptionsDesk,
    american_price, euro_price, invert_american_iv, dtparse, demo
)

NOW = datetime(2026,10,12,14,30,tzinfo=timezone.utc)
C = Contract('SYN_30_100_C',date(2026,11,11),100,'C')


def setmarket(desk, at=NOW):
    desk.on_underlying(at,99.99,100.01)
    tau=30/365
    val=american_price(100,100,tau,.25,.04,.005,'C',desk.cfg.tree_steps)
    desk.market[C.id]=MarketQuote(at,round(val-.08,2),round(val+.08,2),6,6,.25)
    desk.now=at
    return val


def test_put_early_exercise_and_call_parity():
    # US put with positive interest can have early exercise premium.
    american_put=american_price(80,100,.75,.3,.05,0,'P',64)
    european_put=euro_price(80,100,.75,.3,.05,0,'P')
    assert american_put>=european_put-.000001
    assert american_put-european_put>.05
    # For a non-dividend-paying stock early call exercise is suboptimal.
    american_call=american_price(100,100,1,.2,.04,0,'C',200)
    european_call=euro_price(100,100,1,.2,.04,0,'C')
    assert abs(american_call-european_call)<.02


def test_implied_vol_recovers_input():
    tau=30/365
    price=american_price(100,100,tau,.27,.04,.005,'C',48)
    iv=invert_american_iv(price,100,C,tau,.04,.005,48)
    assert abs(iv-.27)<.002


def test_utc_required():
    with pytest.raises(ValueError):dtparse('2026-10-12T14:30:00')
    assert dtparse('2026-10-12T10:30:00-04:00').hour==14


def test_market_data_and_quote_safety():
    desk=OptionsDesk([C]);setmarket(desk)
    desk.on_quote(NOW+timedelta(milliseconds=2),C.id,2.0,2.10,5,5)
    assert C.id in desk.working or desk.rejects.get('invalid_implied_vol',0)
    invalid=OptionsDesk([C]);invalid.on_underlying(NOW,101,100)
    assert invalid.rejects.get('invalid_underlying')==1


def test_no_trade_until_latency_passes():
    cfg=Config(latency_ms=1000)
    desk=OptionsDesk([C],cfg);setmarket(desk)
    desk.working[C.id]=WorkingQuote(NOW,NOW+timedelta(seconds=1),2.95,3.05,3,3,0,0)
    desk.market[C.id]=MarketQuote(NOW,2.96,3.06,5,5,.25)
    desk.on_trade(NOW+timedelta(milliseconds=200),C.id,3.06,5,'BUY')
    assert not desk.fills


def test_best_price_queue_and_partial_fills():
    desk=OptionsDesk([C]);setmarket(desk)
    desk.market[C.id]=MarketQuote(NOW,2.96,3.06,5,5,.25)
    desk.working[C.id]=WorkingQuote(NOW,NOW,2.96,3.06,3,3,2,2)
    desk.on_trade(NOW,C.id,3.06,1,'BUY')  # Queue ahead consumes first print.
    assert not desk.fills
    desk.on_trade(NOW,C.id,3.06,5,'BUY')
    assert len(desk.fills)==1 and desk.fills[0].quantity==3
    assert desk.positions[C.id]==-3
    assert math.isclose(desk.cash,3*100*3.06-3*desk.cfg.option_fee)
    desk.on_trade(NOW,C.id,3.06,4,'BUY')
    assert len(desk.fills)==1  # Remaining order exhausted.


def test_away_market_quote_does_not_fill():
    desk=OptionsDesk([C]);setmarket(desk)
    desk.market[C.id]=MarketQuote(NOW,2.96,3.06,5,5,.25)
    desk.working[C.id]=WorkingQuote(NOW,NOW,2.50,3.50,3,3,0,0)
    desk.on_trade(NOW,C.id,3.06,5,'BUY')
    assert not desk.fills


def test_risk_caps_and_kill_switch():
    desk=OptionsDesk([C],Config(max_per_contract=2));setmarket(desk)
    assert not desk.limits_ok(C.id,3)
    desk.killed=True
    assert not desk.limits_ok(C.id,1)
    desk.positions[C.id]=-1
    assert desk.limits_ok(C.id,1)  # Explicitly risk-reducing buyback.


def test_hedge_is_delayed_until_next_underlying_event():
    desk=OptionsDesk([C],Config(hedge_trigger=1.,max_hedge_per_event=50));setmarket(desk)
    desk.positions[C.id]=4
    desk.snapshot()
    pending=desk.pending_hedge
    assert pending<0 and desk.stock_shares==0
    desk.on_underlying(NOW+timedelta(seconds=1),99.80,99.82)
    assert desk.stock_shares==pending and len(desk.hedges)==1
    assert desk.cash!=0  # Stock hedge incurs actual cash + fees.


def test_snapshot_and_stale_marks():
    desk=OptionsDesk([C]);setmarket(desk)
    desk.positions[C.id]=1
    assert desk.marked_pnl() is not None
    desk.now=NOW+timedelta(seconds=11)
    assert desk.marked_pnl() is None


def test_out_of_order_replay_rejected():
    desk=OptionsDesk([C])
    later=dict(timestamp=(NOW+timedelta(seconds=1)).isoformat(),event='UNDERLYING',bid=99,ask=100)
    earlier=dict(timestamp=NOW.isoformat(),event='UNDERLYING',bid=99,ask=100)
    with pytest.raises(ValueError):desk.run([later,earlier])


def test_deterministic_synthetic_flow():
    c1,r1=demo(29,3);c2,r2=demo(29,3)
    assert c1==c2 and r1==r2
    assert any(r['event']=='TRADE' for r in r1)
    assert r1==sorted(r1,key=lambda e:dtparse(e['timestamp']))


def test_negative_stress_reduces_when_delta_hedged():
    delta_risk=dict(delta=1000.,gamma=0.,vega=0.,gross=0.)
    hedged=dict(delta=0.,gamma=0.,vega=0.,gross=0.)
    assert OptionsDesk.approximate_stress(hedged,100)>OptionsDesk.approximate_stress(delta_risk,100)