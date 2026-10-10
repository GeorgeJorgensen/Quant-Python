"""Monte Carlo V7 regression and reproducibility tests; run with pytest -q."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest
from monte_carlo import (
    REGIMES, instruments, make_tape, run_one, distribution_statistics,
    variant_config, experiment,
)
from desk_options_mm_v7 import OptionsDesk, Config, dtparse


def test_same_seed_is_exactly_reproducible():
    contracts, one, first = make_tape(2026, 6, REGIMES[1])
    contracts_2, two, second = make_tape(2026, 6, REGIMES[1])
    assert contracts == contracts_2
    assert one == two and first == second
    assert make_tape(2027,6,REGIMES[1])[1] != one


def test_synthetic_events_are_sorted_and_have_valid_quotes():
    _,events,_ = make_tape(3, 6,REGIMES[4])
    assert [dtparse(e['timestamp']) for e in events] == sorted(dtparse(e['timestamp']) for e in events)
    for e in events:
        if e['event'] in ('QUOTE','UNDERLYING'):
            assert 0 < e['bid'] < e['ask']
        if e['event']=='TRADE':
            assert e['quantity']>0


def test_market_tape_does_not_have_forecast_fields():
    _,events,_=make_tape(17,6,REGIMES[5])
    forbidden={'future_spot','future_vol','next_price','regime','informed'}
    assert all(not forbidden.intersection(e) for e in events)


def test_stress_regimes_guarantee_a_jump():
    assert make_tape(17,8,REGIMES[4])[2]['shocks'] >= 1
    assert make_tape(17,8,REGIMES[5])[2]['shocks'] >= 1


def test_replay_identical_given_same_market_and_execution_seed():
    contracts, events,_=make_tape(2901,7,REGIMES[0])
    config=variant_config('baseline',2901,16)
    a=OptionsDesk(contracts,config).run(events)
    b=OptionsDesk(contracts,config).run(events)
    assert a.report()==b.report()
    assert [x.price for x in a.fills]==[x.price for x in b.fills]


def test_risk_widens_and_execution_sensitivity_is_named():
    baseline=variant_config('baseline',3,16)
    defensive=variant_config('defensive',3,16)
    fast=variant_config('fast_hedge',3,16)
    assert defensive.min_half_spread>baseline.min_half_spread
    assert fast.hedge_trigger<baseline.hedge_trigger
    assert baseline.inside_capture_probability==.30


def test_monte_carlo_stats_use_observed_tail_losses():
    s=distribution_statistics([-300,-100,30,50,100],bootstrap_seed=7,resamples=400)
    assert s['n']==5
    assert s['loss_expected_shortfall_95']>=s['loss_var_95']
    assert s['worst']==-300
    assert 0<=s['win_rate_wilson_95'][0]<=s['win_rate']<=s['win_rate_wilson_95'][1]<=1
    assert s['mean_bootstrap_ci_95'][0]<=s['mean']<=s['mean_bootstrap_ci_95'][1]
    assert distribution_statistics([10,20,30])['loss_var_95']==0


def test_stats_reject_invalid_numbers():
    with pytest.raises(ValueError): distribution_statistics([])
    with pytest.raises(ValueError): distribution_statistics([0,float('nan')])


def test_comparison_cannot_use_same_strategy_or_too_few_paths(tmp_path):
    with pytest.raises(ValueError):
        experiment(paths=6,steps=4,output=tmp_path,compare='baseline',plots=False,save_worst=False)
    with pytest.raises(ValueError):
        experiment(paths=6,steps=4,output=tmp_path,compare='defensive',plots=False,save_worst=False)


def test_replay_worst_case_and_holdout_are_auditable(tmp_path):
    out=tmp_path/'research'
    summary=experiment(paths=12,steps=5,seed=1,output=str(out),
                       compare='defensive',plots=False,save_worst=True)
    assert summary['paths']==12
    assert len(list(csv.DictReader(open(out/'paths.csv'))))==24
    pairs=list(csv.DictReader(open(out/'paired_comparison.csv')))
    assert len(pairs)==12
    assert sum(p['subset']=='holdout' for p in pairs)>0
    assert summary['comparison_result']['holdout']['n']>0
    worst=summary['worst_case_replay']
    assert (out/'worst_case'/'summary.json').exists()
    assert (out/'worst_case'/'worst_case_events.csv').exists()
    assert (out/'worst_case'/'worst_case_contracts.csv').exists()
    recorded=json.loads((out/'worst_case'/'summary.json').read_text())
    assert recorded['liquidation_pnl']==worst['liquidation_pnl']
    for row in pairs:
        assert abs(float(row['comparison_pnl'])-float(row['primary_pnl'])-
                   float(row['improvement_comparison_minus_primary']))<.011