trades = [
    {
        "ticker": "AAPL",
        "shares": 25,
        "buy_price": 227.50,
        "sell_price": 234.20,
        "commission": 1.00,
    },
    {
        "ticker": "MSFT",
        "shares": 15,
        "buy_price": 418.00,
        "sell_price": 411.50,
        "commission": 1.00,
    },
    {
        "ticker": "NVDA",
        "shares": 40,
        "buy_price": 176.25,
        "sell_price": 181.75,
        "commission": 1.00,
    },
    {
        "ticker": "GOOGL",
        "shares": 10,
        "buy_price": 241.00,
        "sell_price": 239.50,
        "commission": 1.00,
    },
    {
        "ticker": "AMZN",
        "shares": 20,
        "buy_price": 225.40,
        "sell_price": 231.90,
        "commission": 1.00,
    },
]


def format_currency(value):
    if value < 0:
        return f"(${abs(value):,.2f})"
    return f"${value:,.2f}"


def format_percent(value):
    if value < 0:
        return f"({abs(value):.2f}%)"
    return f"{value:.2f}%"


def validate_trade(trade):
    if trade["shares"] <= 0:
        return False
    if trade["buy_price"] <= 0:
        return False
    if trade["sell_price"] <= 0:
        return False
    if trade["commission"] < 0:
        return False
    return True


def calculate_trade(trade):
    initial_value = trade["shares"] * trade["buy_price"]
    final_value = trade["shares"] * trade["sell_price"]
    gross_pnl = final_value - initial_value
    net_pnl = gross_pnl - trade["commission"]
    return_pct = net_pnl / initial_value * 100

    return initial_value, final_value, gross_pnl, net_pnl, return_pct


total_initial = 0
total_final = 0
total_net_pnl = 0
total_return_pct = 0
wins = 0
valid_trades = 0
best_trade = None
worst_trade = None
best_return = None
worst_return = None

for trade in trades:
    if not validate_trade(trade):
        print(f"Invalid trade data for {trade['ticker']}")
        continue

    valid_trades += 1

    initial_value, final_value, gross_pnl, net_pnl, return_pct = calculate_trade(trade)

    if best_return is None or return_pct > best_return:
        best_return = return_pct
        best_trade = trade["ticker"]

    if worst_return is None or return_pct < worst_return:
        worst_return = return_pct
        worst_trade = trade["ticker"]

    total_initial += initial_value
    total_final += final_value
    total_net_pnl += net_pnl
    total_return_pct += return_pct

    if net_pnl > 0:
        wins += 1
        result = "WIN"
    else:
        result = "LOSS"

    print(
        f"{trade['ticker']} | "
        f"Initial: {format_currency(initial_value)} | "
        f"Final: {format_currency(final_value)} | "
        f"Gross P&L: {format_currency(gross_pnl)} | "
        f"Net P&L: {format_currency(net_pnl)} | "
        f"Return: {format_percent(return_pct)} | "
        f"{result}"
    )

if valid_trades > 0:
    win_rate = wins / valid_trades * 100
    average_return = total_return_pct / valid_trades

    print("\nPORTFOLIO SUMMARY")
    print(f"Total Initial Value: {format_currency(total_initial)}")
    print(f"Total Final Value: {format_currency(total_final)}")
    print(f"Total Net P&L: {format_currency(total_net_pnl)}")
    print(f"Win Rate: {format_percent(win_rate)}")
    print(f"Average Trade Return: {format_percent(average_return)}")
    print(f"Best Trade: {best_trade} {format_percent(best_return)}")
    print(f"Worst Trade: {worst_trade} {format_percent(worst_return)}")
else:
    print("No valid trades to analyze.")
