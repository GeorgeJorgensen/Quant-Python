import json
import subprocess


def get_all_holdings():
    """Pull all investment holdings from all linked Plaid Items."""

    result = subprocess.run(
        ["plaid", "investments", "holdings", "--all", "--json"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Plaid command failed:\n{result.stderr}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Plaid returned output that could not be parsed as JSON."
        ) from error


def get_portfolios():
    """Convert Plaid holdings into a dictionary organized by account and ticker."""

    holdings_data = get_all_holdings()
    items = holdings_data["items"]

    eligible_accounts = []

    for item in items:
        for account in item["accounts"]:
            if (
                account["type"] == "investment"
                and account["subtype"] != "crypto exchange"
            ):
                eligible_accounts.append(
                    {
                        "name": account["name"],
                        "account_id": account["account_id"],
                        "type": account["type"],
                        "subtype": account["subtype"],
                    }
                )

    account_lookup = {
        account["account_id"]: account["name"]
        for account in eligible_accounts
    }

    portfolios = {}

    for item in items:
        security_lookup = {
            security["security_id"]: {
                "ticker": security.get("ticker_symbol"),
                "name": security.get("name"),
            }
            for security in item["securities"]
        }

        for holding in item["holdings"]:
            account_id = holding["account_id"]

            if account_id not in account_lookup:
                continue

            security = security_lookup.get(holding["security_id"], {})
            ticker = security.get("ticker")
            security_name = security.get("name")
            account_name = account_lookup[account_id]

            shares = holding.get("quantity", 0)
            price = holding.get("institution_price", 0)
            value = holding.get("institution_value", 0)

            if account_name not in portfolios:
                portfolios[account_name] = {}

            if ticker == "CUR:USD":
                portfolios[account_name]["CASH"] = {
                    "name": "Cash",
                    "shares": shares,
                    "price": price,
                    "value": value,
                }
            elif ticker:
                portfolios[account_name][ticker] = {
                    "name": security_name,
                    "shares": shares,
                    "price": price,
                    "value": value,
                }

    return portfolios


if __name__ == "__main__":
    portfolios = get_portfolios()

    print("\nALL INVESTMENT ACCOUNTS")
    print("=" * 60)

    for account_name, portfolio in portfolios.items():
        print(f"\n{account_name}")
        print("-" * 60)

        total_value = 0

        for ticker, position in portfolio.items():
            print(
                f"{ticker:<8} | "
                f"Shares: {position['shares']:>10.4f} | "
                f"Price: ${position['price']:>10,.2f} | "
                f"Value: ${position['value']:>10,.2f}"
            )
            total_value += position["value"]

        print("-" * 60)
        print(f"Total Account Value: ${total_value:,.2f}")
