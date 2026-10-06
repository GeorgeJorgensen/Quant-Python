# Week 2 — Trading Analytics Toolkit

## Overview

This project builds a reusable Python toolkit for analyzing market microstructure, execution quality, and trading P&L.

The toolkit calculates quoting metrics, execution edge, inventory mark-to-market P&L, and total trading P&L.

## Assignment 2A

`assignment_2a.py` includes functions for:

- Calculating the midpoint between bid and ask
- Calculating the bid/ask spread
- Expressing the spread as a percentage of midpoint
- Measuring execution edge relative to fair value
- Calculating execution P&L
- Calculating inventory P&L after fair value changes
- Combining execution and inventory P&L into net P&L

## Example Scenario

The assignment models a market maker quoting:

- Bid: $74.92
- Ask: $75.08
- Initial fair value: $75.00
- Customer execution: sell 2,500 shares at $75.08
- New fair value: $75.12

The program separates the trade into two economic components:

1. **Execution P&L** — profit earned by executing away from fair value.
2. **Inventory P&L** — gain or loss caused by fair value moving while the dealer holds the resulting position.

In the sample scenario:

- Execution P&L = +$200
- Inventory P&L = -$300
- Net P&L = -$100

This demonstrates an important market-making concept: earning spread at execution does not guarantee a profitable trade if inventory subsequently moves against the dealer.

## Skills Practiced

- Python functions
- Function arguments and return values
- Conditional logic
- Reusing functions inside larger calculations
- Code execution order
- Financial P&L logic
- Market microstructure concepts
- Clean formatted output

## Next Step

Use the Trading Analytics Toolkit as the foundation for a larger market-making simulator with repeated trades, inventory tracking, and deeper P&L analysis.
