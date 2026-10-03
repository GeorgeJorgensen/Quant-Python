"""
assignment_3b.py

Yahoo Finance-backed multi-stock market-making simulator.

Educational goals:
- Download real intraday market prices with yfinance.
- Estimate short-horizon volatility from observed price changes.
- Generate bid/ask quotes around an inventory-adjusted reservation price.
- Simulate customer order flow against those quotes.
- Track long/short inventory, average cost, realized P&L, and unrealized P&L.
- Enforce per-symbol inventory limits.
- Save a detailed trade log for later analysis.

IMPORTANT:
This is an educational simulator only. It does not submit orders to a broker,
exchange, or live trading venue. Yahoo Finance is also not an institutional
real-time execution feed.
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf


# -----------------------------------------------------------------------------
# DEFAULT MODEL UNIVERSE
# -----------------------------------------------------------------------------
# Prices are NOT hardcoded. These symbols simply define which securities the
# program asks Yahoo Finance to download.
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

# Customer order size is simulated because Yahoo provides market prices, not
# the proprietary customer-order flow received by an actual market maker.
DEFAULT_ORDER_SIZES = (100, 200, 500, 1000)


@dataclass(frozen=True)
class StrategyConfig:
    """Central location for market-making and risk parameters."""

    # Maximum absolute inventory allowed in any one security.
    max_position: int = 5_000

    # Minimum total bid/ask spread in basis points.
    min_spread_bps: float = 4.0

    # Controls how strongly recent price volatility widens the spread.
    volatility_spread_multiplier: float = 0.15

    # Safety cap that prevents extreme short-term volatility from producing an
    # absurdly wide quote.
    max_half_spread_bps: float = 50.0

    # Maximum amount that inventory can move the quote center away from the
    # external market midpoint.
    max_inventory_skew_bps: float = 20.0

    # Number of recent replay observations used for volatility estimation.
    volatility_lookback: int = 30

    # U.S. equities generally trade in $0.01 increments.
    tick_size: float = 0.01


@dataclass
class Trade:
    """Stores one accepted simulated customer execution."""

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
    """Download and normalize intraday Yahoo Finance price data."""

    def __init__(
        self,
        tickers: Iterable[str],
        period: str = "5d",
        interval: str = "5m",
    ) -> None:
        # dict.fromkeys removes duplicates while preserving ticker order.
        self.tickers = list(dict.fromkeys(t.upper() for t in tickers))
        self.period = period
        self.interval = interval

        if not self.tickers:
            raise ValueError("At least one ticker is required.")

        self.close = self._download_close_prices()

    def _download_close_prices(self) -> pd.DataFrame:
        """Download Yahoo data once and return a clean close-price matrix."""

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
                "Yahoo Finance returned no data. Check your connection, tickers, "
                "period, and interval."
            )

        close = self._extract_close(raw)
        close = close.sort_index()
        close = close.replace([np.inf, -np.inf], np.nan)

        # Forward filling handles occasional missing timestamps while preserving
        # the most recently observed market price for each stock.
        close = close.ffill().dropna(how="all")

        if close.empty:
            raise RuntimeError("No usable Yahoo Finance prices remained after cleaning.")

        return close

    def _extract_close(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Normalize yfinance single- and multi-ticker output formats."""

        if isinstance(raw.columns, pd.MultiIndex):
            level0 = raw.columns.get_level_values(0)
            level1 = raw.columns.get_level_values(1)

            if "Close" in level0:
                close = raw["Close"].copy()
            elif "Close" in level1:
                close = raw.xs("Close", axis=1, level=1).copy()
            else:
                raise RuntimeError("Could not locate Close prices in Yahoo data.")

            if isinstance(close, pd.Series):
                close = close.to_frame(name=self.tickers[0])

            close.columns = [str(column).upper() for column in close.columns]
            return close

        if "Close" not in raw.columns:
            raise RuntimeError("Could not locate Close prices in Yahoo data.")

        return raw[["Close"]].rename(columns={"Close": self.tickers[0]})

    def available_tickers(self) -> List[str]:
        """Symbols for which Yahoo returned at least one usable price."""
        return [
            symbol
            for symbol in self.close.columns
            if self.close[symbol].notna().any()
        ]

    def latest_prices(self) -> Dict[str, float]:
        """Most recent Yahoo price for every available symbol."""
        prices: Dict[str, float] = {}

        for symbol in self.available_tickers():
            series = self.close[symbol].dropna()
            if not series.empty:
                prices[symbol] = float(series.iloc[-1])

        return prices

    def replay_events(self) -> List[Tuple[pd.Timestamp, str, float]]:
        """Convert the price matrix into timestamp/symbol/price replay events."""
        events: List[Tuple[pd.Timestamp, str, float]] = []

        for timestamp, row in self.close.iterrows():
            for symbol in self.available_tickers():
                price = row.get(symbol)
                if pd.notna(price) and float(price) > 0:
                    events.append((timestamp, symbol, float(price)))

        return events


class MarketMaker:
    """Market-making book for one stock."""

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

        # Inventory convention:
        #   positive position = long shares
        #   negative position = short shares
        self.position = 0

        # For a long, avg_cost is the average purchase price.
        # For a short, avg_cost is the average short-sale price.
        self.avg_cost = 0.0

        self.realized_pnl = 0.0
        self.price_history: List[float] = [self.mid_price]
        self.trades: List[Trade] = []
        self.rejected_orders = 0

    def update_price(self, new_price: float) -> None:
        """Update fair value using a newly observed Yahoo market price."""
        if new_price <= 0 or not math.isfinite(new_price):
            return

        self.mid_price = float(new_price)
        self.price_history.append(self.mid_price)

        max_history = self.config.volatility_lookback + 1
        if len(self.price_history) > max_history:
            self.price_history = self.price_history[-max_history:]

    def estimate_bar_volatility(self) -> float:
        """Estimate standard deviation of recent log returns per replay bar."""
        if len(self.price_history) < 3:
            return 0.0

        prices = np.asarray(self.price_history, dtype=float)
        log_returns = np.diff(np.log(prices))
        log_returns = log_returns[np.isfinite(log_returns)]

        if len(log_returns) < 2:
            return 0.0

        return float(np.std(log_returns, ddof=1))

    def _round_to_tick(self, price: float) -> float:
        """Round a theoretical quote to the configured market tick size."""
        tick = self.config.tick_size
        return round(round(price / tick) * tick, 2)

    def calculate_quotes(self) -> Tuple[float, float]:
        """
        Produce inventory-aware bid and ask quotes.

        The model first chooses a spread based on recent volatility. It then
        shifts the center of that spread based on inventory:

        - Long inventory shifts quotes DOWN, encouraging customers to buy stock
          from us and discouraging us from buying even more.
        - Short inventory shifts quotes UP, encouraging customers to sell stock
          to us so we can buy back the short.
        """
        cfg = self.config
        mid = self.mid_price

        # Convert minimum total spread from basis points into dollars.
        min_half_spread = mid * (cfg.min_spread_bps / 10_000.0) / 2.0

        # More volatile stocks receive wider quotes.
        bar_volatility = self.estimate_bar_volatility()
        volatility_half_spread = (
            mid * bar_volatility * cfg.volatility_spread_multiplier
        )

        half_spread = max(min_half_spread, volatility_half_spread)

        # Cap spread expansion for stability.
        max_half_spread = mid * (cfg.max_half_spread_bps / 10_000.0)
        half_spread = min(half_spread, max_half_spread)

        # Inventory ratio ranges approximately from -1 to +1 while inside the
        # position limit.
        inventory_ratio = self.position / cfg.max_position

        max_skew_dollars = mid * (cfg.max_inventory_skew_bps / 10_000.0)

        # Long inventory => negative shift. Short inventory => positive shift.
        inventory_shift = -inventory_ratio * max_skew_dollars
        reservation_price = mid + inventory_shift

        bid = self._round_to_tick(reservation_price - half_spread)
        ask = self._round_to_tick(reservation_price + half_spread)

        # Tick rounding could theoretically collapse the spread. Guarantee at
        # least one tick between bid and ask.
        if ask <= bid:
            ask = round(bid + cfg.tick_size, 2)

        return bid, ask

    def execute_trade(
        self,
        timestamp: pd.Timestamp,
        customer_side: str,
        shares: int,
    ) -> Optional[Trade]:
        """Execute one simulated customer market order against our quote."""
        customer_side = customer_side.upper()

        if customer_side not in {"BUY", "SELL"}:
            raise ValueError("customer_side must be BUY or SELL.")
        if shares <= 0:
            raise ValueError("shares must be positive.")

        bid, ask = self.calculate_quotes()

        if customer_side == "BUY":
            # Customer buys from us. We sell at our ask, reducing inventory.
            maker_side = "SELL"
            delta_shares = -shares
            fill_price = ask
        else:
            # Customer sells to us. We buy at our bid, increasing inventory.
            maker_side = "BUY"
            delta_shares = shares
            fill_price = bid

        proposed_position = self.position + delta_shares

        # Refuse the trade if it would breach the hard inventory limit.
        if abs(proposed_position) > self.config.max_position:
            self.rejected_orders += 1
            return None

        self._apply_inventory_change(delta_shares, fill_price)

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

    def _apply_inventory_change(self, delta_shares: int, fill_price: float) -> None:
        """Update inventory, average cost, and realized P&L after an execution."""
        old_position = self.position

        # Opening a brand-new long or short position is straightforward.
        if old_position == 0:
            self.position = delta_shares
            self.avg_cost = fill_price
            return

        # If the new trade increases an existing long or short, calculate a
        # weighted-average entry price.
        same_direction = (
            (old_position > 0 and delta_shares > 0)
            or (old_position < 0 and delta_shares < 0)
        )

        if same_direction:
            old_qty = abs(old_position)
            added_qty = abs(delta_shares)
            new_qty = old_qty + added_qty

            self.avg_cost = (
                self.avg_cost * old_qty + fill_price * added_qty
            ) / new_qty
            self.position = old_position + delta_shares
            return

        # Otherwise, this trade is closing some or all of an existing position.
        closing_qty = min(abs(old_position), abs(delta_shares))

        if old_position > 0:
            # Selling a long realizes profit when fill > average cost.
            self.realized_pnl += closing_qty * (fill_price - self.avg_cost)
        else:
            # Buying back a short realizes profit when fill < short-sale price.
            self.realized_pnl += closing_qty * (self.avg_cost - fill_price)

        new_position = old_position + delta_shares
        self.position = new_position

        if new_position == 0:
            self.avg_cost = 0.0
            return

        # If we traded through zero, the excess shares opened a new position at
        # the current fill price.
        flipped = (
            (old_position > 0 > new_position)
            or (old_position < 0 < new_position)
        )
        if flipped:
            self.avg_cost = fill_price

    @property
    def unrealized_pnl(self) -> float:
        """Mark open inventory to the latest Yahoo midpoint."""
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
    """Coordinate market-data replay, customer flow, and all security books."""

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

        self.books: Dict[str, MarketMaker] = {}

        # Start each security from its first real Yahoo observation rather than
        # from a hardcoded price.
        for symbol in market_data.available_tickers():
            series = market_data.close[symbol].dropna()
            if not series.empty:
                self.books[symbol] = MarketMaker(
                    symbol=symbol,
                    initial_price=float(series.iloc[0]),
                    config=config,
                )

        if not self.books:
            raise RuntimeError("No usable securities were initialized.")

        self.accepted_orders = 0
        self.rejected_orders = 0

    def run(self, number_of_orders: int = 100) -> None:
        """Replay Yahoo prices and inject simulated customer market orders."""
        if number_of_orders <= 0:
            raise ValueError("number_of_orders must be positive.")

        events = [
            event
            for event in self.market_data.replay_events()
            if event[1] in self.books
        ]

        if not events:
            raise RuntimeError("No market-data events are available for replay.")

        # Randomly distribute customer orders across genuine historical market
        # observations. The underlying prices and volatility are therefore real
        # Yahoo observations even though customer order arrival is simulated.
        order_event_indices = sorted(
            self.random.randrange(len(events)) for _ in range(number_of_orders)
        )

        next_order = 0

        for event_index, (timestamp, symbol, price) in enumerate(events):
            book = self.books[symbol]
            book.update_price(price)

            while (
                next_order < len(order_event_indices)
                and order_event_indices[next_order] == event_index
            ):
                customer_side = self.random.choice(("BUY", "SELL"))
                shares = self.random.choice(self.order_sizes)

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

        # End the simulation with every book marked to Yahoo's most recent price.
        latest = self.market_data.latest_prices()
        for symbol, book in self.books.items():
            if symbol in latest:
                book.update_price(latest[symbol])

    def trade_log(self) -> pd.DataFrame:
        """Return all accepted simulated executions as a DataFrame."""
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
        """Build one reporting row per stock."""
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
                }
            )

        return pd.DataFrame(rows)

    def print_dashboard(self) -> None:
        """Print a terminal dashboard summarizing current quotes and risk."""
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
    """Define command-line options so the simulator can be easily modified."""
    parser = argparse.ArgumentParser(
        description="Yahoo Finance-backed multi-stock market-making simulator."
    )

    parser.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--orders", type=int, default=100)
    parser.add_argument("--period", default="5d")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--max-position", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--save-trades",
        default="market_maker_trades.csv",
        help="CSV output file for accepted executions.",
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

    config = StrategyConfig(max_position=args.max_position)

    market_data = YahooMarketData(
        tickers=args.tickers,
        period=args.period,
        interval=args.interval,
    )

    print("Loaded:   " + ", ".join(market_data.available_tickers()))

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

    print("\nEducational simulator only. No broker/exchange orders were submitted.")


if __name__ == "__main__":
    main()
