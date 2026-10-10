# Options Market-Making Research Lab — Version 6.0

A **research-grade, event-driven, reproducible options market-making simulator** built as an educational portfolio project. Inspired by *publicly described* trading/research workflows, **not** Citadel Securities code, architecture, model, or an exchange-connected trading system.

**Safety:** No broker login, order-routing connector, live order code, API keys, or permissions to send transactions. All fills, queue shares, and hedge executions are hypothetical. **Do not interpret a profitable synthetic demo as evidence of alpha.**

## Features

| Area | Implementation | Why it matters |
|---|---|---|
| Instrument registry | Option ID, strike, expiry, call/put, explicit multiplier | Avoid ticker/expiry confusion |
| Pricing | American CRR binomial tree with early-exercise nodes | US equity-style option approximation |
| Pricing Greeks | Finite-difference delta, gamma, vega using same theoretical pricer | Full portfolio sensitivities |
| Implied volatility | American-price inversion from observed option NBBO mids | Derive surface from *data*, not canned values |
| Surface | Causal ridge-regression smile/term fit, IV bounds | Mark risk from options observed thus far |
| Quoting | Theoretical value, uncertainty half-spread, option inventory and delta skew | Risk-sensitive two-sided liquidity |
| Execution | Quote activation latency, inside/touch eligibility, queue-ahead depletion, partial simulated fills | Avoid instantly filling every quote |
| Risk | Per-series contracts, delta, gamma, vega, premium exposure, approximate scenario guard | Reject risk-increasing breaches |
| Kill switch | Liquidation-mark loss circuit breaker, allow strictly inventory-reducing trades | Controls after loss |
| Hedging | Next-underlying-event hedge with bid/ask, slippage, stock fees, inventory cap | Avoid same-observation fills |
| Analytics | Every fill, delayed hedges, marked vs liquidating P&L, next-quote markout, drawdown, full reprice shock scenarios | Diagnose negative edge, costs and risk |
| Replay | Timezone-aware, ordered CSV event stream; reproducible built-in demo | Causality and auditability |
| Verification | Pytest unit/integration tests | Make changes safely |

## Quick start

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
python -m pytest -q test_desk_options_mm.py
python desk_options_mm.py --demo --steps 42 --output demo_results
```

Expected outputs: `demo_results/summary.json`, `fills.csv`, `hedges.csv`, `risk.csv`.

## Replaying your OWN licensed historical quotes and trade prints

```bash
python desk_options_mm.py --contracts example_contracts.csv --events example_events.csv --output replay_results
```

`example_events.csv` is **SYNTHETIC**. You must acquire any real historical data and confirm its licensing. The script never downloads or fabricates real exchange prices. A market quote feed alone cannot establish actual maker fills.

**`contracts.csv`** columns:

`id,expiry,strike,right,multiplier`

- `expiry`: ISO date `YYYY-MM-DD`
- `right`: `C` for call, `P` for put
- `multiplier`: 100 in standard unadjusted equity contracts, but input may vary
- `id`: your own stable contract identifier (not necessarily OCC symbology)

**`events.csv`** columns:

`timestamp,event,contract_id,bid,ask,bid_size,ask_size,price,quantity,aggressor`

- `timestamp`: offset-aware ISO-8601, such as `2026-10-12T14:30:00+00:00`
- `event=UNDERLYING`: `bid`, `ask` refer to underlying stock; leave other fields blank
- `event=QUOTE`: `contract_id`, option `bid`, option `ask`, `bid_size`, `ask_size`
- `event=TRADE`: `contract_id`, executed `price`, `quantity`, and supplied aggressor `BUY` or `SELL`
- **Order rows strictly by timestamp**. For equal timestamps, source feed ordering matters. No exchange order-sequence IDs are supported.
- `aggressor` side must come from source data or a documented classification model. Do **not** label quote-derived guessed aggressor as authoritative.
- Market data is assumed to be a consolidated, internally consistent option NBBO; an institutional implementation would need venue-specific order books.

## Event sequencing (no look-ahead)

1. **Underlying quote**: execute hedge instruction from prior snapshot against new underlying bid/ask, cancel stale working option quotes.
2. **Option quote**: validate observations, infer American IV, fit causal cross-sectional surface, publish new risk-skewed simulated quotes after latency.
3. **Option trade print**: check that aggressor is consistent with touch price; consume a simplified queue-ahead model; enforce limits; apply hypothetical maker fill and fees.
4. **After event**: record fully observed P&L and portfolio Greeks if all open positions have fresh marks; set a pending hedge for the **next** underlying update.
5. **After next quote**: compute one-step markout of earlier maker fills as a **diagnostic**, never a trading signal available at the time of the prior fill.

## Mathematical concepts to study

- Risk exposure in delta equivalent shares: `sum(contracts × multiplier × option delta) + stock shares`.
- Dollar gamma per $1 spot move: `sum(contracts × multiplier × option gamma)`.
- Vega per **1.00** implied volatility move: `sum(contracts × multiplier × option vega)`; divide by 100 for 1 volatility point.
- Risk-aware reservation price: modeled fair value minus a function of *signed* option inventory and portfolio delta.
- American option value: backwards induction `max(early exercise value, discounted expected continuation value)`.
- Marked P&L: `cash + stock_shares × stock_mid + Σ(qty × multiplier × option_mid)`.
- Liquidation P&L uses stock bid/ask and option bid for long / ask for short positions; it assumes the displayed quote is executable for the full position, which may overstate true recoverable value.
- First-next-quote markout: `side_sign × (next_market_mid - our_fill_price) × contracts × multiplier`. It includes initial spread capture; interpret alongside costs and model errors.

## Limits — NOT production ready

There are serious missing dependencies that cannot be invented from a code example:

- Proprietary exchange multicast feeds, native timestamps, order acknowledgements, sequence-gap recovery, and order-by-order queue reconstruction.
- Actual maker exchange matching rules, hidden and routed liquidity, cancellations, rejected/canceled orders, OPRA latency, venue allocation and complex-order interactions.
- Complete option reference data, corporate actions, discrete dividend forecasts, borrow costs, settlement and expiration/exercise/assignment workflows.
- No-arbitrage constrained stochastic/local-vol calibration, volatility dynamics, overnight gap/earnings modeling, funding/capital and portfolio margin.
- Low-latency compiled execution stack, process isolation, high availability, compliance surveillance, market-access controls, authorized kill switch, post-trade reconciliation, SRE monitoring and disaster recovery.
- Robust out-of-sample calibration against actual historical *trades*, order-book events, latencies, costs, exchange fees/rebates and historical execution probability.

Model limitations: gamma from finite CRR tree and a 2% spot bump is approximate; American continuous dividend yield is not discrete-dividend valuation; the smile regression is **not arbitrage-free**; quotes are periodically replaced rather than exchange-grade order lifecycle; the queue-fill model is illustrative; shock pre-trade check uses a Delta-Gamma-Vega Taylor approximation; full-reprice stress appears in the final report but is not the pre-trade circuit breaker.

Real brokers/dealers providing market access must implement applicable risk and supervisory systems. This project is not a substitute for those systems.

## Recommended research progression

1. Ingest timestamped, licensed venue-level option quote/trade data; verify symbol mapping, UTC alignment, timestamps and missing/duplicate events.
2. Replace approximate queue fill simulator with venue-specific queue/reorder/cancel data; quantify fill-selection bias against passive quotes.
3. Calibrate/validate an arbitrage-constrained implied-volatility surface with discrete dividends and financing inputs; compare model residuals out of sample.
4. Add price/research attribution: short gamma, long/short vega, implied vs realized vol edge, hedge costs, jump risk, and impact.
5. Build tests for trading halts, high volatility, gap moves, partial option/hedge fills, out-of-order feeds, and exchange outages.

## Public references

- Citadel Securities career description: https://www.citadelsecurities.com/careers/details/systematic-options-trader/
- OCC standard equity options specifications: https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications
- SEC market access guidance: https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0