# Quant-Python

This repository contains my Python coursework, quantitative finance exercises, trading tools, and projects as I prepare for quantitative trading roles.

## Featured Projects

### Trading Analytics Toolkit
Built reusable market-microstructure and trade-analysis tools that calculate:

- Bid/ask midpoint
- Dollar and percentage spread
- Execution edge versus fair value
- Execution P&L
- Inventory mark-to-market P&L
- Net trading P&L
- Formatted trade reports

The toolkit separates execution quality from inventory risk and is designed as a reusable foundation for larger trading simulations.

### Market-Making Simulator
Built a Python market-making simulator using historical market data across large-cap U.S. equities.

The simulator:

- Downloads multi-symbol market data
- Generates bid/ask quotes around a calculated midpoint
- Simulates repeated order flow
- Tracks position and average cost
- Calculates realized, unrealized, and total P&L
- Monitors spread capture and inventory risk
- Produces a multi-symbol market-making dashboard

The project currently runs across AAPL, MSFT, NVDA, AMZN, GOOGL, META, AVGO, TSLA, BRK-B, and JPM.

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

### Week 2 — Trading Analytics Toolkit
Built the core market-microstructure calculations used in later assignments, including execution edge, spread analysis, inventory P&L, and net trading P&L.

### Week 3 — Trading Analytics + Market-Making Simulator
Expanded the trading toolkit into a formatted reporting module and then built a multi-symbol market-making simulator using real historical equity data.

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
│   ├── Week 2/
│   │   ├── assignment_2a.py
│   │   └── README.md
│   └── Week 3/
│       ├── assignment_3a.py
│       ├── assignment_3b.py
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

Active coursework. The Trading Analytics Toolkit and Market-Making Simulator are complete and will continue to be expanded with additional trading, options, and strategy-research projects.
