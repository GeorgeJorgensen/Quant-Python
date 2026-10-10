# Institutional-style Options Market Making — Research Platform V8

> **Safety boundary:** Offline research only. No broker connection, no order-entry APIs, no exchange connectivity, no live trading and no claim of production compliance or profitability. This is an *institutional-style research prototype*, not a deployable institutional market-making system or a copy of any firm's proprietary code.

The focus of V8 is research discipline, operational control and measurement, not superficial complexity. It upgrades V7's American-option valuation, causal event replay, inventory-skewed quotes, simulated queue fills, delayed delta hedging, stress checks and stochastic-regime Monte Carlo with a validated data pipeline, auditable decisions, a reconciled cash ledger, and chronological out-of-sample analysis.

## Quick start

Python 3.11+ recommended.

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
python3 institutional_run.py --demo --steps 36 --output results_v8
python3 monte_carlo.py --paths 60 --steps 18 --compare defensive --output monte_carlo_v8
```

For a fast offline Monte Carlo smoke test:

```bash
python3 monte_carlo.py --paths 12 --steps 10 --tree-steps 8 --compare defensive --no-plots --no-worst-case --output mc_smoke
```

To replay *user-provided* timestamped events:

```bash
python3 institutional_run.py \
  --contracts sample_data/contracts.csv \
  --events sample_data/events.csv \
  --policy baseline \
  --output replay_from_csv
```

The two sample CSVs are **synthetic**, not a real exchange tape. Merely supplying CSVs does not establish market-data authenticity.

To perform strict chronological model-selection research on **at least five full UTC feed-days** (each day's first event must be an `UNDERLYING` quote):

```bash
python3 institutional_run.py \
  --contracts YOUR_CONTRACTS.csv --events YOUR_EVENTS.csv \
  --walkforward --output historical_oos
```

The strategy set is deliberately limited to baseline, defensive, and fast_hedge. The first 60% of dates select the policy by mean daily liquidation P&L; later dates are untouched during selection and used as holdout. Positions are reset to flat at every UTC date boundary. For actual market research, use proper exchange-session calendars, avoid splitting an RTH session across UTC dates, model expiry/assignment, and use significantly more than five days. These limitations are explicit.

## What's included

| File | Responsibility |
|---|---|
| `desk_options_mm.py` | Original options market-making/event-driven pricing engine with additional quote and rejection audits, fail-closed kill switch |
| `institutional_run.py` | Strict feed quality, replay controls, full P&L bridge, reproducibility manifest, daily chronological out-of-sample model selection |
| `monte_carlo.py` | Paired regime-dependent stochastic-volatility / jump Monte Carlo based on V7, with held-out analysis |
| `test_engine.py` | Original engine regression suite |
| `test_monte_carlo.py` | Monte Carlo regression suite |
| `test_institutional.py` | New 22 checks of accounting, data, kill switch, chronology, provenance |
| `sample_data/` | Clearly labeled synthetic contract reference and event tape |
| `example_replay/` | Reproducible single-run example of all reporting artifacts |
| `example_monte_carlo/` | Small paired-scenario demonstration; not statistically conclusive |

## Event CSV contract

Every event must have an ISO-8601 timezone-aware timestamp (e.g., `2026-10-12T14:30:00+00:00`) and already be in chronological order. Rows with identical timestamps retain file order. Events are not sorted silently because that can hide corrupt ingestion.

Required `events.csv` header:

`timestamp,event,contract_id,bid,ask,bid_size,ask_size,price,quantity,aggressor`

- **UNDERLYING**: timestamp, event, bid, ask. This must precede option quotes at the start of each session.
- **QUOTE**: timestamp, event, contract_id, bid, ask, bid_size, ask_size.
- **TRADE**: timestamp, event, contract_id, price, quantity, aggressor (`BUY`/`SELL`). An observed trade is *not* proof that a particular passive order was filled.

Reference `contracts.csv` header:

`id,expiry,strike,right,multiplier`

- `right` is `C` or `P`, and `expiry` is ISO date. Currently modeled as an American equity option with a continuous dividend yield approximation.
- The research engine currently has **one underlying** and does not support option adjustments, early assignment notices, exercise, expiry or discrete corporate-action mechanics.
- The tape validator checks structural validity, timestamps, quote crosses, missing contracts, impossible prices, outlier underlying moves and print sequencing. It cannot independently certify market data provenance, exchange NBBO correctness or market conditions.

## Interpreting the reports

`institutional_run.py --demo` creates:

- `summary.json`: raw engine performance/risk metrics and repricing stress scenarios.
- `institutional_summary.json`: raw engine metrics plus the P&L bridge and source label.
- `attribution.json`: additive P&L reconciliation.
- `manifest.json`: run fingerprint, source label, configuration, validation summary and documented limitations.
- `quote_audit.csv`: quotes produced by the model, reference quotes, estimates and activation times.
- `rejections.csv`: rejected order/market input events, timestamps, reasons (may be absent if no rejections).
- `fills.csv`, `hedges.csv`, `risk.csv`: complete raw strategy event logs (may be absent if empty).
- `tape/`: copied input source for exact replay.

**P&L bridge identity:**

```text
Net marked P&L
    = option spread-at-fill proxy
    + option post-fill marked movement
    + stock hedge execution vs contemporaneous midpoint
    + stock post-hedge marked movement
    - option commissions
    - stock commissions
```

The bridge reconciles to `cash + final stock inventory at midpoint + option inventory at midpoint`, within numerical rounding from exported hedge price precision. This is a dollar accounting bridge, not proof the model has alpha. Markout and spread are not independent forecasts. Liquidation P&L marks long options at the outside bid and shorts at the outside ask, so it can be materially worse.

### Key causal assumptions

1. Option fair value is computed with an American binomial tree and an approximate, shrinkage-based volatility surface inferred from past/current option quotes. The surface is **not guaranteed arbitrage-free**.
2. Quote activation has a configured delay; observed trades only lead to *hypothetical* fills after simplified queue competition and probabilistic inside-spread capture. Venue-specific routing, hidden orders, queue cancels and trade-through rules are not modeled.
3. Stock hedges execute on the **next underlying quote**, not at the current midpoint. Fees, bid/ask and slippage are included.
4. Risk caps reject proposed risk-increasing fills when exposures or approximate stress loss exceed configured limits. A triggered portfolio loss circuit breaker removes passive quotes; **it does not automatically liquidate positions**.
5. For Monte Carlo, the exogenous generator can construct informed customer flow using a *future simulated move*. That information is only in the external generator, never passed into market-maker logic. The model and execution parameters are not calibrated to observed exchange data.

## Why this is not production-ready

Institutional trading typically also requires licensed consolidated and venue-level feeds; complete OCC/exchange reference data; dividends/borrow/corporate-action, exercise and assignment handling; order acknowledgments/rejects/cancels with exchange sequence numbers and recovery; real-time independent pre-trade credit and regulatory checks; venue-specific orderbook position reconstruction; clearing/margin/settlement reconciliation; operational supervision, surveillance, entitlements, restricted-list controls, and documented governance and monitoring.

For US broker-dealers with market access, SEC Rule 15c3-5 applies pre-trade risk controls to market-maker quotes as well as other orders. This project **does not implement or certify compliance** with that rule. References: https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0 and OCC options disclosure at https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document.

## Most valuable next investment

Acquire suitable historical, timestamped **option NBBO, underlying NBBO and trades**, license and validate reference data, calibrate the queue/fill model, test by venue and session with out-of-sample periods, and commission an independent reconciliation/risk review. More Monte Carlo paths cannot compensate for a wrong market generator or inaccurate fills.