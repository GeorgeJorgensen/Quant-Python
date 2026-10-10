# Options Market-Making Research Platform — Version 7

**An event-driven American-options research simulator plus reproducible multi-regime Monte Carlo validation.** Built for studying quantitative options-trading decisions. Inspired by publicly discussed market-making problems; **NOT Citadel Securities software, not market-connected, and not production ready**.

**You cannot trade real money with this project.** It has no broker integration, order submission function, accounts, credentials, native exchange feed, or market access controls. All generated prices, prints, fills, and P&L are hypothetical. Winning on simulated tapes is not evidence of profitability on real exchanges.

## Files

| File | Purpose |
|---|---|
| `desk_options_mm_v7.py` | Event-driven options desk: American CRR pricing, Greeks, market IV/smile, inventory/delta-aware quotes, latency, queue-ahead, risk limits, hedging and P&L |
| `monte_carlo.py` | Synthetic spot, stochastic volatility, jumps, informed flow, Monte Carlo, empirical risk metrics, confidence intervals and paired policy tests |
| `test_desk_options_mm_v7.py` | Existing valuation, risk, causality and execution tests |
| `test_monte_carlo.py` | Reproducibility, regime, P&L statistics, scenario replay and holdout tests |
| `requirements.txt` | Python dependencies |
| `example_monte_carlo/` | An illustrative simulation output (if packaged) |

## Quick start

Python **3.10+** recommended.

```bash
python -m pip install -r requirements.txt
python -m pytest -q test_desk_options_mm_v7.py test_monte_carlo.py
```

Single, reproducible Version 7 tape:

```bash
python desk_options_mm_v7.py --demo --steps 42 --output single_replay
```

### Monte Carlo mode

Explore **60** market paths, each containing 24 five-minute steps:

```bash
python monte_carlo.py --paths 60 --steps 24 --seed 2901 --output mc_results
```

Run on the **same 60 tapes** with two policies, recording paired changes. The second variant quotes wider and carries tighter position risk limits:

```bash
python monte_carlo.py --paths 60 --steps 24 --compare defensive --seed 2901 --output mc_comparison
```

Try fast hedging versus the baseline on common scenarios:

```bash
python monte_carlo.py --paths 60 --steps 24 --compare fast_hedge --output hedge_comparison
```

Compare with intentionally tighter spreads (a research stress test, **not** an endorsement):

```bash
python monte_carlo.py --paths 60 --steps 24 --compare tight_quotes --output tight_spread_test
```

A small sanity run:

```bash
python monte_carlo.py --paths 12 --steps 8 --compare defensive --output smoke_test
```

Explore **two expirations** (12 option contracts, slower/more costly):

```bash
python monte_carlo.py --paths 60 --steps 24 --expiries 2 --tree-steps 24 --output richer_chain
```

CLI options: `--paths`, `--steps`, `--seed`, `--strategy`, `--compare`, `--expiries`, `--tree-steps`, `--output`, `--no-worst-case`, `--no-plots`.

## How a scenario is generated

1. Pick one of six **explicitly defined** regimes: **calm**, **normal**, **trend_up**, **trend_down**, **crash**, **squeeze**. They are assigned approximately equally, **not** according to observed market probabilities.
2. Generate stochastic annualized spot volatility using a mean-reverting log-volatility process with negative return/volatility shock correlation (−0.60). Volatility is bounded for numerical stability, not calibrated.
3. Simulate lognormal underlying returns on a 5-minute trading-time clock, including compound-Poisson jumps and mandatory regime shocks for crash/squeeze paths.
4. Generate a synthetic American-options NBBO from the spot, the simulated IV smile/term structure, and an American binomial pricer. Liquidity/spreads worsen in stressed regimes.
5. Generate hypothetical customer prints. Their aggressor side is correlated with *future* market changes to simulate adverse selection. This dependence exists **only in the external tape generator**, never in the desk's information set.
6. Deliver every event in chronological order to the existing options-desk risk engine. It sees only current and earlier market quotes and prints.
7. Revalue inventory, measure mid/liquidation P&L, track next-quote markouts, hedge deltas on a later underlying event and reject risk-increasing trades that breach limits.

The V7 desk adds a **seeded inside-spread fill-capture assumption**, default 30% for plausible market prints. This is *not exchange queue reconstruction*. Printed trades at the NBBO, hidden size, order priority, and routing do not prove fills on our hypothetical order. Sensitivity to the capture assumption should be tested against real, licensed order-book data.

### Fast Monte Carlo fidelity caveat

The Monte Carlo command defaults to **16 American binomial steps**, intentionally favoring experimentation speed over pricing convergence. Option NBBO generation uses 24 steps. Increase `--tree-steps 48` or more to check convergence; use a much more sophisticated pricer, dividends and stock borrow assumptions before professional evaluation. The default single-replay desk uses 48 steps and remains useful for inspecting one path more carefully.

## Result files and how to interpret them

The Monte Carlo command writes:

- `paths.csv`: one row per simulated scenario and strategy, including seed, regime, marked P&L, **liquidation P&L**, drawdown, fill counts, hedging, markout, exposures, kill-switch and rejections.
- `summary.json`: mean, median, 5th and 1st percentile P&L, standard deviation, profitable-path share with Wilson 95% interval, 95% **bootstrap CI for mean**, **loss VaR95**, **expected shortfall95**, extreme paths, kill-switch frequency and regime-conditioned distributions.
- `paired_comparison.csv`: when `--compare` is used, **comparison P&L minus baseline P&L** on the **same tape** and prespecified development/holdout partition.
- `pnl_distribution.png` and `regime_pnl_boxplot.png`: optional plots (requires matplotlib).
- `worst_case/`: instrument registry, chronological original input tape, full engine's `fills.csv`, `hedges.csv`, `risk.csv`, and `summary.json`, all sufficient to **replay and audit the worst synthetic scenario**.

**Interpretation:**

- `mean` is average *per-path simulated P&L*, not annual return and not live performance.
- `loss_var_95 = max(0, -5th percentile of liquidation P&L)`; this is **dollar loss**, not a statistical confidence interval.
- `loss_expected_shortfall_95 = max(0, -average P&L below/equal to the empirical 5th percentile)`. With very small samples the 5% tail contains few observations; results will be unstable.
- `mean_bootstrap_ci_95` and `win_rate_wilson_95` are *simulation sampling error estimates*, **not** intervals accounting for model misspecification, regime weights, quote-feed errors or real-world strategy uncertainty.
- `comparison_result.holdout.mean` is the mean paired uplift on the **prespecified** holdout tapes; a negative result means the alternative did worse. This is a **synthetic holdout**, not true out-of-sample historical validation.
- `kill_switch_rate` is the fraction of paths where the model stopped adding risk after its hypothetical liquidation loss limit.
- `average_first_next_quote_markout` includes any initially quoted edge before the next observed option midpoint; negative values may indicate adverse selection.

## Research discipline

A plausible model must not simply optimize mean P&L and ignore risk. Inspect expected shortfall, loss tails, adverse selection, stress-case outcomes, execution-quality assumptions and how much fill volume disappears with wider quotes. Don't pick strategy parameters on the holdout. Be skeptical of favorable results on artificial tape generators; variations in quote priority and customer order flow can reverse rankings.

### What remains necessary before any institutional or live trading claim

- Data-quality checks on actual venue-level market data, exchange-specific queue position, real timestamps, cancel/replace acknowledgments, OPRA/SIP latency, lost/reordered packets and true fill attribution.
- American option models with discrete dividends, borrow, exercise/assignment, corporate actions, rates curves, settlements and calibration to current, arbitrage-constrained option surfaces.
- Multivariate correlations, clustering, stochastic jumps, intraday volatility seasonality and regime-probability weights estimated **out of sample** from licensed data (not invented constants).
- Better execution and hedging impact models: real maker fees/rebates, complex-option orders, order priority, routing, latency distributions, implied/hidden liquidity, slippage, tail liquidity.
- Portfolio-level risk and portfolio margin, ongoing scenario generation, recovery after crashes, extreme-event controls, fault injection, regulatory market-access controls and emergency supervision.
- Realistic independent historical out-of-sample validation and forward paper monitoring. The model is not expected to meet low-latency production requirements in Python.

## Code study sequence

In `desk_options_mm_v7.py`: `american_price`, `invert_american_iv`, `surface_iv`, `on_quote`, `on_trade`, `limits_ok`, `snapshot`, `on_underlying`, `report`.

In `monte_carlo.py`: `Regime`, `make_tape`, `variant_config`, `run_one`, `distribution_statistics`, `experiment`, `maybe_plot`.

Study the risks when you change `inside_capture_probability`, `min_half_spread`, `max_abs_delta`, `max_abs_vega`, `hedge_trigger`, `stock_slippage_bps` and `tree_steps`.

**Disclosure:** Built as an independent educational research project. It does not use proprietary code or data from Citadel Securities, All Options, exchanges, or brokers.