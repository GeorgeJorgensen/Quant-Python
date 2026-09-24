# Quant-Python

This repository contains my Python coursework, quantitative finance exercises, trading tools, and projects as I prepare for quantitative trading roles.

## Current Progress

### Week 1 — Portfolio Rebalancing Engine
Built a multi-account portfolio rebalancing workflow that:

- Retrieves investment holdings through Plaid
- Calculates current and target portfolio weights
- Measures allocation drift
- Generates BUY, SELL, and HOLD recommendations
- Handles fractional- and whole-share rebalancing
- Calculates post-trade tracking error
- Produces a formatted Excel report
- Supports automated email delivery

### Week 2 — Trading Utility Functions
Built reusable market-microstructure functions for:

- Bid/ask midpoint
- Dollar spread
- Spread percentage
- Execution edge versus fair value
- Execution P&L
- Inventory mark-to-market P&L
- Net market-maker P&L

The Week 2 work is the foundation for a larger market-making and trading simulation.

## Goals

- Build strong Python fundamentals for quantitative finance and trading
- Strengthen probability, statistics, and numerical reasoning
- Develop reusable trading and market-microstructure tools
- Build portfolio, options, and execution simulations
- Create backtests and research workflows
- Finish with an employer-ready quantitative trading project

## Repository Structure

```text
Quant-Python/
├── Assignments/
│   ├── Week 1/
│   │   ├── assignment_1a.py
│   │   ├── assignment_1b.py
│   │   ├── assignment_1b_questions.md
│   │   ├── plaid_data.py
│   │   └── README.md
│   └── Week 2/
│       ├── assignment_2a.py
│       └── README.md
├── .gitignore
└── README.md
```

## Topics

This repository will continue to expand into:

- Python fundamentals
- NumPy and pandas
- Probability and statistics
- Expected value and risk
- Financial data analysis
- Portfolio mathematics
- Market microstructure
- Execution and inventory P&L
- Options and derivatives
- Backtesting
- Trading strategy research
- Performance and risk analysis

## Status

Active coursework. Week 1 and the Week 2 Wednesday checkpoint are complete, with larger trading-simulation work continuing in Week 2.
