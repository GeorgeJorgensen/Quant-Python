# Week 3 — Trading Analytics Toolkit & Market-Making Simulator

## Assignment 3A — Trading Analytics Toolkit

This assignment expands the core market-microstructure calculations into a reusable reporting module.

### Functions included

- `midpoint(bid, ask)`
- `spread(bid, ask)`
- `spread_percent(bid, ask)`
- `execution_edge(fill_price, midpoint_price, side)`
- `execution_pnl(edge_per_share, shares)`
- `inventory_pnl(start_price, end_price, shares)`
- `net_pnl(execution_pnl_value, inventory_pnl_value)`

### Example scenario

- Bid: $74.92
- Ask: $75.08
- Midpoint: $75.00
- Shares: 2,500
- Buy fill: $74.92
- Ending price: $74.88

### Results

- Spread: $0.16
- Spread %: 0.2133%
- Execution edge: $0.08/share
- Execution P&L: +$200
- Inventory P&L: -$300
- Net P&L: -$100

The module separates execution quality from inventory risk and provides a reusable foundation for larger trading simulations.

## Assignment 3B — Market-Making Simulator

`assignment_3b.py` builds on the Trading Analytics Toolkit to simulate market making across large-cap U.S. equities using historical market data.

The simulator:

- Downloads market data for multiple symbols
- Generates bid and ask quotes around a midpoint
- Simulates repeated order flow
- Tracks inventory and average cost
- Calculates realized, unrealized, and total P&L
- Measures spread capture and inventory risk
- Produces a multi-symbol market-making dashboard

The current universe includes AAPL, MSFT, NVDA, AMZN, GOOGL, META, AVGO, TSLA, BRK-B, and JPM.

## Main Lesson

Market-making profitability depends on more than earning the bid/ask spread. A strategy must also manage inventory risk, adverse price movement, and position exposure while maintaining consistent execution.
