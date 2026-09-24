# Assignment 2A
# Trading Utility Functions
# George Jorgensen


def calculate_midpoint(bid, ask):
    midpoint = (bid + ask) / 2
    return midpoint


def calculate_spread(bid, ask):
    spread = ask - bid
    return spread


def calculate_spread_percentage(bid, ask):
    midpoint = calculate_midpoint(bid, ask)
    spread = calculate_spread(bid, ask)
    spread_percentage = (spread / midpoint) * 100
    return spread_percentage


def calculate_execution_edge(execution_price, fair_value, side):
    if side == "sell":
        edge = execution_price - fair_value
    elif side == "buy":
        edge = fair_value - execution_price

    return edge


def calculate_execution_pnl(edge_per_share, shares):
    execution_pnl = edge_per_share * shares
    return execution_pnl


def calculate_inventory_pnl(shares, old_fair_value, new_fair_value, side):
    if side == "sell":
        inventory_pnl = (old_fair_value - new_fair_value) * shares
    elif side == "buy":
        inventory_pnl = (new_fair_value - old_fair_value) * shares

    return inventory_pnl


def calculate_net_pnl(execution_pnl, inventory_pnl):
    net_pnl = execution_pnl + inventory_pnl
    return net_pnl


# Trade inputs
bid = 74.92
ask = 75.08
fair_value = 75.00
execution_price = 75.08
side = "sell"
shares = 2500
new_fair_value = 75.12


# Calculations
midpoint = calculate_midpoint(bid, ask)
spread = calculate_spread(bid, ask)
spread_percentage = calculate_spread_percentage(bid, ask)

execution_edge = calculate_execution_edge(
    execution_price,
    fair_value,
    side
)

execution_pnl = calculate_execution_pnl(
    execution_edge,
    shares
)

inventory_pnl = calculate_inventory_pnl(
    shares,
    fair_value,
    new_fair_value,
    side
)

net_pnl = calculate_net_pnl(
    execution_pnl,
    inventory_pnl
)


# Output
print(f"Bid: ${bid:.2f}")
print(f"Ask: ${ask:.2f}")
print(f"Midpoint: ${midpoint:.2f}")
print(f"Spread: ${spread:.2f}")
print(f"Spread %: {spread_percentage:.4f}%")
print(f"Execution Edge: ${execution_edge:.2f} per share")
print(f"Execution P&L: ${execution_pnl:,.2f}")
print(f"Inventory P&L: ${inventory_pnl:,.2f}")
print(f"Net P&L: ${net_pnl:,.2f}")
