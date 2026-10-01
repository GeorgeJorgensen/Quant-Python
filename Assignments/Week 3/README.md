# Week 3 — Trading Calculations Module

## Assignment 3A

This assignment turns core market-microstructure calculations into reusable Python functions that can later be integrated into a larger market-making simulator.

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

### Main lesson

A trader can earn positive execution edge and still lose money overall if inventory moves against the position. The assignment separates execution quality from inventory risk and combines both into total trading P&L.

This module is designed to become a building block for the larger market-making simulator.
