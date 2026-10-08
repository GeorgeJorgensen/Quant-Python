
"""
assignment_4a.py — Week 4 risk-based position sizing

A Yahoo Finance-backed market-making simulator for educational use.

Core ideas demonstrated:
- Downloading real market data with yfinance.
- Replaying recent intraday bars rather than hardcoding prices.
- Estimating short-horizon volatility from observed returns.
- Quoting a bid and ask around an inventory-skewed reservation price.
- Enforcing position limits.
- Tracking average inventory cost, realized P&L, unrealized P&L, and total P&L.
- Simulating customer market orders against the market maker's quotes.

IMPORTANT:
This is a simulator. It does NOT send orders to a broker or exchange.
Yahoo/yfinance data is not an exchange-grade real-time market-data feed.
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf


DEFAULT_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "AVGO",
    "TSLA",
    "BRK-B",
    "JPM",
]

DEFAULT_ORDER_SIZES = (100, 200, 500, 1000)


def calculate_order_size(capital: float, price: float,
                         max_position_pct: float, current_inventory: int,
                         side: str, max_position_shares: int = 5_000) -> int:
    """Maximum whole-share order under BOTH dollar and share position limits.

    Current inventory is signed: long positive, short negative.
    The side is the MARKET MAKER'S trade side, not the customer's.
    We assume a symmetric short/long position limit and permit crossing flat.
    This is exposure sizing, not a cash/margin or portfolio-wide risk model.
    """
    if not all(math.isfinite(x) for x in (capital, price, max_position_pct)):
        raise ValueError("Capital, price and percentage must be finite.")
    if capital <= 0 or price <= 0 or not 0 <= max_position_pct <= 1:
        raise ValueError("Positive capital/price and percentage 0..1 required.")
    if isinstance(current_inventory, bool) or not isinstance(current_inventory, int):
        raise ValueError("Inventory must be an integer number of shares.")
    if max_position_shares < 0:
        raise ValueError("Share cap must not be negative.")
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("Maker side must be BUY or SELL.")

    # First cap dollar exposure, then preserve the original fixed-share cap.
    dollar_cap = capital * max_position_pct
    max_shares = min(math.floor(dollar_cap / price), max_position_shares)

    # A BUY increases signed inventory; a SELL decreases inventory.
    # Example: cap=40, inventory=15 -> BUY 25 or SELL 55 maximum.
    capacity = max_shares - current_inventory if side == "BUY" else max_shares + current_inventory
    return max(0, capacity)




@dataclass(frozen=True)
class StrategyConfig:
    # Total simulated capital and maximum PER-SYMBOL notional exposure.
    strategy_capital: float = 100_000.0
    max_position_pct: float = 0.10
    # Original hard cap remains as a second safeguard.
    max_position: int = 5_000
    min_spread_bps: float = 4.0
    volatility_spread_multiplier: float = 0.15
    max_half_spread_bps: float = 50.0
    max_inventory_skew_bps: float = 20.0
    volatility_lookback: int = 30
    tick_size: float = 0.01


@dataclass
class Trade:
    timestamp: pd.Timestamp
    symbol: str
    customer_side: str
    maker_side: str
    shares: int
    fill_price: float
    mid_price: float
    position_after: int
    realized_pnl_after: float


class YahooMarketData:
    """
    Downloads and normalizes recent intraday data from Yahoo Finance.

    The object downloads data once for all requested symbols so the simulation
    does not repeatedly hit Yahoo for every order.
    """

    def __init__(
        self,
        tickers: Iterable[str],
        period: str = "5d",
        interval: str = "5m",
    ) -> None:
        self.tickers = list(dict.fromkeys(t.upper() for t in tickers))
        self.period = period
        self.interval = interval

        if not self.tickers:
            raise ValueError("At least one ticker is required.")

        self.close = self._download_close_prices()

    def _download_close_prices(self) -> pd.DataFrame:
        """
        Download adjusted intraday prices.

        auto_adjust=True makes the returned OHLC series reflect corporate
        actions. For a short intraday replay this usually has little practical
        impact, but it keeps the price series internally consistent.
        """
        raw = yf.download(
            tickers=self.tickers,
            period=self.period,
            interval=self.interval,
            auto_adjust=True,
            progress=False,
            group_by="column",
            threads=True,
        )

        if raw.empty:
            raise RuntimeError(
                "Yahoo Finance returned no data. Check your internet connection, "
                "ticker symbols, period, and interval."
            )

        close = self._extract_close(raw)

        # Standardize the index so all symbols share one replay timeline.
        close = close.sort_index()
        close = close.replace([np.inf, -np.inf], np.nan)

        # Forward-fill only after a symbol has started trading in this window.
        # This helps with occasional missing timestamps between symbols.
        close = close.ffill()

        # Remove rows where every security is unavailable.
        close = close.dropna(how="all")

        if close.empty:
            raise RuntimeError("No usable close-price data remained after cleaning.")

        return close

    def _extract_close(self, raw: pd.DataFrame) -> pd.DataFrame:
        """
        yfinance can return either:
        - a normal single-level DataFrame for one ticker, or
        - a MultiIndex DataFrame for multiple tickers.

        This helper converts both cases into:
            index = timestamps
            columns = ticker symbols
            values = close prices
        """
        if isinstance(raw.columns, pd.MultiIndex):
            level0 = raw.columns.get_level_values(0)
            level1 = raw.columns.get_level_values(1)

            if "Close" in level0:
                close = raw["Close"].copy()
            elif "Close" in level1:
                close = raw.xs("Close", axis=1, level=1).copy()
            else:
                raise RuntimeError("Could not locate Close prices in Yahoo data.")

            # A one-column extraction can occasionally become a Series.
            if isinstance(close, pd.Series):
                close = close.to_frame(name=self.tickers[0])

            close.columns = [str(c).upper() for c in close.columns]
            return close

        # Single-ticker response.
        if "Close" not in raw.columns:
            raise RuntimeError("Could not locate Close prices in Yahoo data.")

        return raw[["Close"]].rename(columns={"Close": self.tickers[0]})

    def available_tickers(self) -> List[str]:
        """Return symbols that contain at least one real downloaded observation."""
        return [
            symbol
            for symbol in self.close.columns
            if self.close[symbol].notna().any()
        ]

    def latest_prices(self) -> Dict[str, float]:
        """Return the most recent available price for every usable symbol."""
        prices: Dict[str, float] = {}

        for symbol in self.available_tickers():
            series = self.close[symbol].dropna()
            if not series.empty:
                prices[symbol] = float(series.iloc[-1])

        return prices

    def replay_events(self) -> List[Tuple[pd.Timestamp, str, float]]:
        """
        Convert the wide price matrix into timestamp/symbol/price events.

        Each event represents an observed Yahoo close for one interval.
        """
        events: List[Tuple[pd.Timestamp, str, float]] = []

        for timestamp, row in self.close.iterrows():
            for symbol in self.available_tickers():
                price = row.get(symbol)
                if pd.notna(price) and float(price) > 0:
                    events.append((timestamp, symbol, float(price)))

        return events


class MarketMaker:
    """
    One market-making book for one security.

    Position convention:
        positive = market maker is LONG shares
        negative = market maker is SHORT shares

    Customer order convention:
        customer BUY  -> market maker SELLS at the ask
        customer SELL -> market maker BUYS at the bid
    """

    def __init__(
        self,
        symbol: str,
        initial_price: float,
        config: StrategyConfig,
    ) -> None:
        if initial_price <= 0:
            raise ValueError("initial_price must be positive.")

        self.symbol = symbol.upper()
        self.config = config

        self.mid_price = float(initial_price)
        self.position = 0

        # avg_cost means:
        # - average purchase price when long
        # - average short-sale price when short
        self.avg_cost = 0.0

        self.realized_pnl = 0.0
        self.price_history: List[float] = [self.mid_price]
        self.trades: List[Trade] = []
        self.rejected_orders = 0

    def update_price(self, new_price: float) -> None:
        """Update fair value using the newest observed Yahoo market price."""
        if new_price <= 0 or not math.isfinite(new_price):
            return

        self.mid_price = float(new_price)
        self.price_history.append(self.mid_price)

        # We only need enough history for the rolling volatility estimate.
        max_history = self.config.volatility_lookback + 1
        if len(self.price_history) > max_history:
            self.price_history = self.price_history[-max_history:]

    def estimate_bar_volatility(self) -> float:
        """
        Estimate standard deviation of log returns per replay bar.

        This is deliberately a short-horizon volatility estimate rather than
        annualized volatility because our quote spread applies to the current
        short trading interval.
        """
        if len(self.price_history) < 3:
            return 0.0

        prices = np.asarray(self.price_history, dtype=float)
        returns = np.diff(np.log(prices))
        returns = returns[np.isfinite(returns)]

        if len(returns) < 2:
            return 0.0

        return float(np.std(returns, ddof=1))

    def calculate_quotes(self) -> Tuple[float, float]:
        """
        Calculate inventory-aware bid and ask quotes.

        Step 1: Build a base spread from:
            - a minimum spread floor
            - recent observed volatility

        Step 2: Shift the quote center ("reservation price") based on inventory:
            - long inventory  -> shift quotes DOWN to encourage selling
            - short inventory -> shift quotes UP to encourage buying

        Step 3: Round to the configured market tick size.
        """
        mid = self.mid_price
        cfg = self.config

        # Minimum total spread in dollars.
        min_total_spread = mid * (cfg.min_spread_bps / 10_000.0)
        min_half_spread = min_total_spread / 2.0

        # Convert recent one-bar volatility into a dollar spread component.
        bar_volatility = self.estimate_bar_volatility()
        volatility_half_spread = (
            mid * bar_volatility * cfg.volatility_spread_multiplier
        )

        half_spread = max(min_half_spread, volatility_half_spread)

        # Prevent a noisy observation from creating absurd quotes.
        max_half_spread = mid * (cfg.max_half_spread_bps / 10_000.0)
        half_spread = min(half_spread, max_half_spread)

        # Measure inventory relative to today's effective share limit.
        # Using a dynamically sized limit makes quote skew align with risk.
        effective_limit = min(cfg.max_position, math.floor(
            cfg.strategy_capital * cfg.max_position_pct / mid
        ))
        inventory_ratio = self.position / max(1, effective_limit)
        inventory_ratio = max(-1.0, min(1.0, inventory_ratio))

        # A long position produces a negative center shift.
        # A short position produces a positive center shift.
        skew_dollars = (
            inventory_ratio
            * cfg.max_inventory_skew_bps
            / 10_000.0
            * mid
        )

        reservation_price = mid - skew_dollars

        bid = self._round_to_tick(reservation_price - half_spread)
        ask = self._round_to_tick(reservation_price + half_spread)

        # Enforce a valid market even after tick rounding.
        if ask <= bid:
            ask = self._round_to_tick(bid + cfg.tick_size)

        return bid, ask

    def _round_to_tick(self, price: float) -> float:
        """Round a price to the nearest permitted tick."""
        tick = self.config.tick_size
        return round(round(price / tick) * tick, 10)

    def can_execute(self, customer_side: str, shares: int) -> bool:
        """Check whether a customer order would violate the position limit."""
        customer_side = customer_side.upper()

        if customer_side not in {"BUY", "SELL"}:
            raise ValueError("customer_side must be BUY or SELL.")
        if shares <= 0:
            raise ValueError("shares must be positive.")

        # Customer BUY means the maker SELLS; customer SELL means maker BUYS.
        maker_side = "SELL" if customer_side == "BUY" else "BUY"
        allowed = calculate_order_size(
            self.config.strategy_capital, self.mid_price,
            self.config.max_position_pct, self.position, maker_side,
            self.config.max_position,
        )
        return shares <= allowed

    def execute_trade(
        self,
        timestamp: pd.Timestamp,
        customer_side: str,
        shares: int,
    ) -> Optional[Trade]:
        """
        Execute one simulated customer market order against our current quote.

        Returns:
            Trade object if accepted.
            None if rejected by the position limit.
        """
        customer_side = customer_side.upper()

        if not self.can_execute(customer_side, shares):
            self.rejected_orders += 1
            return None

        bid, ask = self.calculate_quotes()

        if customer_side == "BUY":
            # Customer lifts our ask; we sell.
            maker_side = "SELL"
            maker_delta = -shares
            fill_price = ask
        else:
            # Customer hits our bid; we buy.
            maker_side = "BUY"
            maker_delta = shares
            fill_price = bid

        self._apply_inventory_trade(maker_delta, fill_price)

        trade = Trade(
            timestamp=timestamp,
            symbol=self.symbol,
            customer_side=customer_side,
            maker_side=maker_side,
            shares=shares,
            fill_price=fill_price,
            mid_price=self.mid_price,
            position_after=self.position,
            realized_pnl_after=self.realized_pnl,
        )

        self.trades.append(trade)
        return trade

    def _apply_inventory_trade(self, delta_shares: int, fill_price: float) -> None:
        """
        Update position, average cost, and realized P&L.

        delta_shares:
            positive -> market maker buys
            negative -> market maker sells

        This method correctly handles:
        - adding to a long
        - adding to a short
        - partially closing
        - fully closing
        - flipping from long to short
        - flipping from short to long
        """
        old_position = self.position

        if old_position == 0:
            self.position = delta_shares
            self.avg_cost = fill_price
            return

        same_direction = (
            (old_position > 0 and delta_shares > 0)
            or (old_position < 0 and delta_shares < 0)
        )

        if same_direction:
            old_qty = abs(old_position)
            add_qty = abs(delta_shares)
            new_qty = old_qty + add_qty

            self.avg_cost = (
                self.avg_cost * old_qty + fill_price * add_qty
            ) / new_qty

            self.position = old_position + delta_shares
            return

        # The trade is reducing or reversing an existing position.
        closing_qty = min(abs(old_position), abs(delta_shares))

        if old_position > 0:
            # Closing a long: profit if sale price > average purchase cost.
            self.realized_pnl += closing_qty * (fill_price - self.avg_cost)
        else:
            # Closing a short: profit if repurchase price < average short price.
            self.realized_pnl += closing_qty * (self.avg_cost - fill_price)

        new_position = old_position + delta_shares
        self.position = new_position

        if new_position == 0:
            self.avg_cost = 0.0
            return

        # If the sign changed, the excess quantity opened a brand-new position
        # at the current execution price.
        flipped = (old_position > 0 > new_position) or (
            old_position < 0 < new_position
        )

        if flipped:
            self.avg_cost = fill_price
        # If we merely reduced the existing position, avg_cost does not change.

    @property
    def unrealized_pnl(self) -> float:
        """Mark the current open inventory to the latest Yahoo mid price."""
        if self.position > 0:
            return self.position * (self.mid_price - self.avg_cost)

        if self.position < 0:
            return abs(self.position) * (self.avg_cost - self.mid_price)

        return 0.0

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def inventory_value(self) -> float:
        """Signed market value of inventory."""
        return self.position * self.mid_price

    @property
    def spread(self) -> float:
        bid, ask = self.calculate_quotes()
        return ask - bid

    @property
    def spread_bps(self) -> float:
        if self.mid_price == 0:
            return 0.0
        return self.spread / self.mid_price * 10_000.0


class MarketMakingSimulation:
    """Coordinates market data, security books, order flow, and reporting."""

    def __init__(
        self,
        market_data: YahooMarketData,
        config: StrategyConfig,
        order_sizes: Tuple[int, ...] = DEFAULT_ORDER_SIZES,
        seed: Optional[int] = 42,
    ) -> None:
        self.market_data = market_data
        self.config = config
        self.order_sizes = order_sizes
        self.random = random.Random(seed)

        latest = market_data.latest_prices()
        if not latest:
            raise RuntimeError("No usable Yahoo prices were found.")

        # Initialize each book from the first actual observation, not a
        # hardcoded value. During replay update_price() will advance it.
        self.books: Dict[str, MarketMaker] = {}

        for symbol in market_data.available_tickers():
            first_valid = market_data.close[symbol].dropna()
            if not first_valid.empty:
                self.books[symbol] = MarketMaker(
                    symbol=symbol,
                    initial_price=float(first_valid.iloc[0]),
                    config=config,
                )

        self.accepted_orders = 0
        self.rejected_orders = 0

    def run(self, number_of_orders: int = 100) -> None:
        """
        Replay real Yahoo prices and inject simulated customer market orders.

        Order arrival is simulated because Yahoo provides market data, not a
        historical customer-order feed. Prices and volatility, however, come
        from downloaded market observations.
        """
        if number_of_orders <= 0:
            raise ValueError("number_of_orders must be positive.")

        events = [
            event
            for event in self.market_data.replay_events()
            if event[1] in self.books
        ]

        if not events:
            raise RuntimeError("There are no market-data events to replay.")

        # Spread N customer orders across the actual historical event stream.
        # Sampling event indices creates different names/times while keeping
        # all executions anchored to genuine observed Yahoo prices.
        order_event_indices = sorted(
            self.random.randrange(len(events)) for _ in range(number_of_orders)
        )

        next_order = 0

        for event_index, (timestamp, symbol, price) in enumerate(events):
            book = self.books[symbol]
            book.update_price(price)

            # Multiple customer orders may happen at the same market event.
            while (
                next_order < len(order_event_indices)
                and order_event_indices[next_order] == event_index
            ):
                customer_side = self.random.choice(("BUY", "SELL"))
                requested_shares = self.random.choice(self.order_sizes)

                # Determine how many shares the maker may accept at this price.
                # A customer's BUY is a market-maker SELL, and vice versa.
                maker_side = "SELL" if customer_side == "BUY" else "BUY"
                capacity = calculate_order_size(
                    self.config.strategy_capital, book.mid_price,
                    self.config.max_position_pct, book.position, maker_side,
                    self.config.max_position,
                )
                shares = min(requested_shares, capacity)

                # A zero-capacity order cannot be filled or sent to execute_trade.
                # Otherwise simulate a reduced/partial fill, not an over-limit fill.
                if shares == 0:
                    self.rejected_orders += 1
                    book.rejected_orders += 1
                    next_order += 1
                    continue

                trade = book.execute_trade(
                    timestamp=timestamp,
                    customer_side=customer_side,
                    shares=shares,
                )

                if trade is None:
                    self.rejected_orders += 1
                else:
                    self.accepted_orders += 1

                next_order += 1

        # Mark every book to its most recent real Yahoo price at the end.
        latest = self.market_data.latest_prices()
        for symbol, book in self.books.items():
            if symbol in latest:
                book.update_price(latest[symbol])

    def trade_log(self) -> pd.DataFrame:
        """Return every accepted simulated execution as a pandas DataFrame."""
        rows = []

        for book in self.books.values():
            for trade in book.trades:
                rows.append(
                    {
                        "timestamp": trade.timestamp,
                        "symbol": trade.symbol,
                        "customer_side": trade.customer_side,
                        "maker_side": trade.maker_side,
                        "shares": trade.shares,
                        "fill_price": trade.fill_price,
                        "mid_price": trade.mid_price,
                        "position_after": trade.position_after,
                        "realized_pnl_after": trade.realized_pnl_after,
                    }
                )

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

    def summary(self) -> pd.DataFrame:
        """Create one dashboard row per security."""
        rows = []

        for symbol, book in sorted(self.books.items()):
            bid, ask = book.calculate_quotes()

            rows.append(
                {
                    "Symbol": symbol,
                    "Mid": book.mid_price,
                    "Bid": bid,
                    "Ask": ask,
                    "Spread bps": book.spread_bps,
                    "Position": book.position,
                    "Avg Cost": book.avg_cost,
                    "Inventory Value": book.inventory_value,
                    "Realized P&L": book.realized_pnl,
                    "Unrealized P&L": book.unrealized_pnl,
                    "Total P&L": book.total_pnl,
                    "Trades": len(book.trades),
                    "Rejected": book.rejected_orders,
                }
            )

        return pd.DataFrame(rows)

    def print_dashboard(self) -> None:
        """Print a readable terminal dashboard."""
        summary = self.summary()

        print("\n" + "=" * 118)
        print("MARKET MAKING DASHBOARD".center(118))
        print("=" * 118)

        header = (
            f"{'Symbol':<8}"
            f"{'Mid':>11}"
            f"{'Bid':>11}"
            f"{'Ask':>11}"
            f"{'Sprd(bp)':>10}"
            f"{'Position':>11}"
            f"{'Avg Cost':>12}"
            f"{'Real P&L':>13}"
            f"{'Unreal P&L':>14}"
            f"{'Total P&L':>13}"
        )

        print(header)
        print("-" * 118)

        for _, row in summary.iterrows():
            print(
                f"{row['Symbol']:<8}"
                f"{row['Mid']:>11.2f}"
                f"{row['Bid']:>11.2f}"
                f"{row['Ask']:>11.2f}"
                f"{row['Spread bps']:>10.2f}"
                f"{int(row['Position']):>11,d}"
                f"{row['Avg Cost']:>12.2f}"
                f"{row['Realized P&L']:>13.2f}"
                f"{row['Unrealized P&L']:>14.2f}"
                f"{row['Total P&L']:>13.2f}"
            )

        print("-" * 118)

        total_inventory = float(summary["Inventory Value"].sum())
        gross_inventory = float(summary["Inventory Value"].abs().sum())
        realized = float(summary["Realized P&L"].sum())
        unrealized = float(summary["Unrealized P&L"].sum())
        total_pnl = float(summary["Total P&L"].sum())

        print(f"Accepted orders:       {self.accepted_orders:>15,d}")
        print(f"Rejected orders:       {self.rejected_orders:>15,d}")
        print(f"Net inventory value:   ${total_inventory:>14,.2f}")
        print(f"Gross inventory value: ${gross_inventory:>14,.2f}")
        print(f"Realized P&L:          ${realized:>14,.2f}")
        print(f"Unrealized P&L:        ${unrealized:>14,.2f}")
        print(f"TOTAL P&L:             ${total_pnl:>14,.2f}")
        print("=" * 118)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Yahoo Finance-backed multi-stock market-making simulator."
    )

    parser.add_argument(
        "--tickers",
        nargs="+",
        default=DEFAULT_TICKERS,
        help="Ticker symbols to trade.",
    )

    parser.add_argument(
        "--orders",
        type=int,
        default=100,
        help="Number of simulated customer orders.",
    )

    parser.add_argument(
        "--period",
        default="5d",
        help="Yahoo Finance lookback period, e.g. 1d, 5d, 1mo.",
    )

    parser.add_argument(
        "--interval",
        default="5m",
        help="Yahoo Finance bar interval, e.g. 1m, 5m, 15m.",
    )

    parser.add_argument(
        "--capital", type=float, default=100_000.0,
        help="Total simulated strategy capital (default $100,000).",
    )
    parser.add_argument(
        "--max-position-pct", type=float, default=0.10,
        help="Maximum per-symbol notional as a decimal of capital (default 0.10).",
    )
    parser.add_argument(
        "--max-position",
        type=int,
        default=5_000,
        help="Maximum absolute shares per symbol.",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for repeatable simulated order flow.",
    )

    parser.add_argument(
        "--save-trades",
        default="market_maker_trades.csv",
        help="CSV output path for accepted simulated trades.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    print("=" * 72)
    print("YAHOO FINANCE MARKET-MAKING SIMULATOR".center(72))
    print("=" * 72)
    print(f"Tickers:  {', '.join(args.tickers)}")
    print(f"Period:   {args.period}")
    print(f"Interval: {args.interval}")
    print(f"Orders:   {args.orders}")
    print("\nDownloading Yahoo Finance data...")

    config = StrategyConfig(
        strategy_capital=args.capital,
        max_position_pct=args.max_position_pct,
        max_position=args.max_position,
    )
    if args.capital <= 0 or not (0 <= args.max_position_pct <= 1) or args.max_position <= 0:
        raise ValueError("Invalid capital, percent or share cap.")
    print(f"Capital:  ${args.capital:,.2f}")
    print(f"Per-stock limit: {args.max_position_pct:.1%} of capital")

    market_data = YahooMarketData(
        tickers=args.tickers,
        period=args.period,
        interval=args.interval,
    )

    print(
        "Loaded:   "
        + ", ".join(market_data.available_tickers())
    )

    simulation = MarketMakingSimulation(
        market_data=market_data,
        config=config,
        seed=args.seed,
    )

    simulation.run(number_of_orders=args.orders)
    simulation.print_dashboard()

    trades = simulation.trade_log()

    if not trades.empty:
        trades.to_csv(args.save_trades, index=False)
        print(f"\nTrade log saved to: {args.save_trades}")
    else:
        print("\nNo trades were accepted, so no trade log was written.")

    print(
        "\nEducational simulator only. "
        "No broker/exchange orders were submitted."
    )


def run_sizing_tests() -> None:
    """Offline, reproducible tests of the new risk-based sizing rules."""
    cases = [
        ("AAPL", 250.0, 15, "BUY", 25),
        ("MSFT", 500.0, 8, "BUY", 12),
        ("NVDA", 200.0, -20, "SELL", 30),
    ]
    print("\nSIZING TESTS: Capital $100,000, position limit 10%")
    for symbol, price, inventory, side, expected in cases:
        actual = calculate_order_size(100_000, price, .10, inventory, side)
        assert actual == expected, f"{symbol}: expected {expected}, got {actual}"
        print(f"{symbol}: {side} {actual} shares allowed")
    # Both sides of a symmetric inventory limit.
    assert calculate_order_size(100_000, 250, .10, 40, "BUY") == 0
    assert calculate_order_size(100_000, 250, .10, 15, "SELL") == 55
    assert calculate_order_size(100_000, 250, .10, -40, "SELL") == 0
    assert calculate_order_size(100_000, 200, .10, -20, "BUY") == 70
    # Original hard cap of 5 shares should override a larger dollar cap.
    assert calculate_order_size(100_000, 250, .10, 0, "BUY", 5) == 5
    print("All sizing tests passed.")


if __name__ == "__main__":
    # Offline test mode does not download from Yahoo Finance.
    import sys
    if "--test-sizing" in sys.argv:
        run_sizing_tests()
    else:
        main()