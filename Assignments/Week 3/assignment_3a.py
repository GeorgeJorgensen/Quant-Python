# ============================================================
# Quant Python
# Week 3 - Assignment 3A
# Trading Calculations Module
#
# Purpose:
# Build reusable Python functions for common trading calculations.
#
# These functions calculate:
#   1. Midpoint
#   2. Bid-ask spread
#   3. Spread percentage
#   4. Execution edge
#   5. Execution P&L
#   6. Inventory P&L
#   7. Net trading P&L
#
# These functions will eventually be reusable inside a larger
# market-making / trading simulator.
# ============================================================


def midpoint(bid, ask):
    """Calculate the midpoint between the bid and ask."""
    return (bid + ask) / 2


def spread(bid, ask):
    """Calculate the dollar bid-ask spread."""
    return ask - bid


def spread_percent(bid, ask):
    """Calculate the bid-ask spread as a percentage of midpoint."""
    mid = midpoint(bid, ask)
    dollar_spread = spread(bid, ask)
    return (dollar_spread / mid) * 100


def execution_edge(fill_price, midpoint_price, side):
    """Calculate execution edge relative to the midpoint."""
    side = side.lower()

    if side == "buy":
        return midpoint_price - fill_price
    elif side == "sell":
        return fill_price - midpoint_price
    else:
        raise ValueError("Side must be 'buy' or 'sell'.")


def execution_pnl(edge_per_share, shares):
    """Convert per-share execution edge into total execution P&L."""
    return edge_per_share * shares


def inventory_pnl(start_price, end_price, shares):
    """Calculate mark-to-market P&L from holding inventory."""
    price_change = end_price - start_price
    return price_change * shares


def net_pnl(execution_pnl_value, inventory_pnl_value):
    """Combine execution P&L and inventory P&L."""
    return execution_pnl_value + inventory_pnl_value


# ============================================================
# TEST DATA
# ============================================================
# Example market:
# Bid = $74.92
# Ask = $75.08
# Buy 2,500 shares at the bid, then mark inventory at $74.88.
# ============================================================

bid = 74.92
ask = 75.08
shares = 2500
side = "buy"
fill_price = 74.92
ending_price = 74.88


# Run each calculation and store the result.
mid = midpoint(bid, ask)
dollar_spread = spread(bid, ask)
spread_pct = spread_percent(bid, ask)
edge = execution_edge(fill_price, mid, side)
exec_pnl = execution_pnl(edge, shares)
inv_pnl = inventory_pnl(mid, ending_price, shares)
total_pnl = net_pnl(exec_pnl, inv_pnl)


# ============================================================
# DISPLAY RESULTS
# ============================================================

print("========================================")
print("        TRADING CALCULATION REPORT")
print("========================================")
print(f"Bid:              ${bid:.2f}")
print(f"Ask:              ${ask:.2f}")
print("----------------------------------------")
print(f"Midpoint:         ${mid:.2f}")
print(f"Spread:           ${dollar_spread:.2f}")
print(f"Spread %:          {spread_pct:.4f}%")
print("----------------------------------------")
print(f"Trade Side:        {side.upper()}")
print(f"Fill Price:       ${fill_price:.2f}")
print(f"Shares:            {shares:,}")
print("----------------------------------------")
print(f"Execution Edge:   ${edge:.2f} per share")
print(f"Execution P&L:    ${exec_pnl:,.2f}")
print(f"Inventory P&L:    ${inv_pnl:,.2f}")
print("----------------------------------------")
print(f"Net P&L:          ${total_pnl:,.2f}")
print("========================================")


# ============================================================
# INTERPRETATION
# ============================================================
# Midpoint = $75.00
# Buy fill = $74.92
# Execution edge = $0.08/share
# Execution P&L = +$200
# Inventory P&L = -$300
# Net P&L = -$100
#
# Key concept:
# Positive execution edge does not guarantee positive total P&L.
# Inventory risk can outweigh the spread or execution edge earned.
# ============================================================
