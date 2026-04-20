"""
Kraken CLI execution worker.

Uses subprocess to call the Kraken CLI binary (or MCP server mode).
Paper / sandbox mode: prepend --sandbox to every CLI call.

Environment variables:
    KRAKEN_CLI_PATH     Path to kraken binary (default: "kraken")
    KRAKEN_API_KEY      Kraken API key
    KRAKEN_API_SECRET   Kraken API secret
    TRADE_MODE          "paper" (default) or "live"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

from src.core.base_worker import BaseWorker
from src.models import (
    Direction, ExecutionLayer, ExecutionResult, MarketData, OHLCV,
    Order, PortfolioState, Position,
)

logger = logging.getLogger(__name__)

# Pair name mapping: agent format → Kraken CLI pair symbol
_PAIR_MAP: dict[str, str] = {
    "BTC/USD":   "XBTUSD",
    "ETH/USD":   "ETHUSD",
    "BTC/USDC":  "XBTUSDC",
    "ETH/USDC":  "ETHUSDC",
    "WETH/USDC": "ETHUSDC",
    "WBTC/USDC": "XBTUSDC",
}

# Reverse map: Kraken internal → USD-equivalent scale factor
# Kraken often uses XXBTZUSD or XETHZUSD internally; we strip any X/Z prefixes
_BALANCE_USD_PAIRS: dict[str, str] = {
    "ZUSD":  "USD",
    "USDC":  "USD",
    "XXBT":  "BTC",
    "XETH":  "ETH",
    "XBT":   "BTC",
    "ETH":   "ETH",
}


def _kraken_pair(pair: str) -> str:
    """Convert agent pair name to Kraken CLI pair symbol."""
    mapped = _PAIR_MAP.get(pair)
    if not mapped:
        # Best-effort: strip "/" and use as-is
        mapped = pair.replace("/", "")
    return mapped


class KrakenWorker(BaseWorker):
    """
    Wraps the Kraken CLI (Rust binary) via subprocess.

    All paper-mode orders use --sandbox; the CLI must be configured with
    valid API credentials (read-only key is enough for market data).
    """

    def __init__(self) -> None:
        self._cli = os.getenv("KRAKEN_CLI_PATH", "kraken")
        self._sandbox = os.getenv("TRADE_MODE", "paper") == "paper"
        # In-memory trade tracker for daily stats (resets on process restart)
        self._daily_trades: list[dict] = []
        self._last_trade_ts: int = 0

    @property
    def name(self) -> str:
        return "kraken"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run(self, *args: str) -> dict[str, Any]:
        """
        Run a Kraken CLI command and return the parsed JSON response.

        In sandbox mode, --sandbox is prepended automatically for order
        commands (market data commands don't need it).
        """
        cmd = [self._cli] + list(args)
        logger.debug("Kraken CLI: %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except FileNotFoundError:
            raise RuntimeError(
                f"Kraken CLI binary not found at '{self._cli}'. "
                "Install it: https://github.com/krakenfx/kraken-cli"
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Kraken CLI timed out after 30s")

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise RuntimeError(f"Kraken CLI error (exit {proc.returncode}): {err}")

        raw = stdout.decode().strip()
        if not raw:
            raise RuntimeError("Kraken CLI returned empty output")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Kraken CLI JSON parse error: {e} | raw: {raw[:200]}")

        # Unwrap {"error": [], "result": {...}} envelope if present
        if isinstance(data, dict) and "result" in data:
            errors = data.get("error", [])
            if errors:
                raise RuntimeError(f"Kraken API error: {errors}")
            return data["result"]
        return data

    def _sandbox_args(self) -> list[str]:
        """Return --sandbox prefix for order commands when in paper mode."""
        return ["--sandbox"] if self._sandbox else []

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_market_data(self, pair: str, num_candles: int = 100) -> MarketData:
        """
        Fetch ticker + 60-min OHLCV candles from Kraken CLI.

        CLI commands:
            kraken market ticker --pair XBTUSD
            kraken market ohlc   --pair XBTUSD --interval 60
        """
        kraken_pair = _kraken_pair(pair)

        # --- Ticker ---------------------------------------------------------
        ticker_raw = await self._run("market", "ticker", "--pair", kraken_pair)

        # Kraken may key the result as the pair or with X/Z prefix variants
        ticker = self._extract_pair_data(ticker_raw, kraken_pair)
        if ticker is None:
            raise RuntimeError(
                f"Pair '{kraken_pair}' not found in ticker response: {list(ticker_raw.keys())}"
            )

        # "c" = [last_trade_price, lot_volume]
        # "b" = [bid_price, ...]
        # "a" = [ask_price, ...]
        # "v" = [volume_today, volume_24h]
        current_price = float(ticker["c"][0])
        bid = float(ticker["b"][0]) if "b" in ticker else None
        ask = float(ticker["a"][0]) if "a" in ticker else None
        volume_24h = float(ticker["v"][1]) if "v" in ticker else None

        # --- OHLCV ----------------------------------------------------------
        ohlc_raw = await self._run(
            "market", "ohlc",
            "--pair", kraken_pair,
            "--interval", "60",
        )
        ohlc_data = self._extract_pair_data(ohlc_raw, kraken_pair)
        if ohlc_data is None or not isinstance(ohlc_data, list):
            logger.warning("OHLC data missing for %s — using single-candle fallback", kraken_pair)
            ohlc_data = []

        # Kraken OHLC row: [time, open, high, low, close, vwap, volume, count]
        candles: list[OHLCV] = []
        for row in ohlc_data[-num_candles:]:
            try:
                candles.append(OHLCV(
                    timestamp=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[6]),
                ))
            except (IndexError, ValueError, TypeError) as e:
                logger.debug("Skipping malformed OHLC row %s: %s", row, e)

        # Ensure at least one candle so downstream indicators don't crash
        if not candles:
            candles = [OHLCV(
                timestamp=int(time.time()),
                open=current_price,
                high=current_price,
                low=current_price,
                close=current_price,
                volume=0.0,
            )]

        return MarketData(
            pair=pair,
            candles=candles,
            current_price=current_price,
            bid=bid,
            ask=ask,
            volume_24h=volume_24h,
            source=ExecutionLayer.KRAKEN,
        )

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    async def get_portfolio(self) -> PortfolioState:
        """
        Fetch account balance and open positions from Kraken CLI.

        CLI commands:
            kraken account balance
            kraken account positions
        """
        # --- Balances -------------------------------------------------------
        balance_raw = await self._run("account", "balance")
        # balance_raw: {"ZUSD": "9823.45", "XXBT": "0.15023", ...}

        usd_balance = 0.0
        for key, val in balance_raw.items():
            normalized = key.lstrip("XZ").upper()
            if normalized in ("USD", "USDC", "USDT"):
                usd_balance += float(val)

        # --- Open positions -------------------------------------------------
        open_positions: list[Position] = []
        try:
            pos_raw = await self._run("account", "positions")
            # pos_raw: {"POSITION_ID": {"pair": "XBTUSD", "type": "buy", "vol": "0.01",
            #                            "cost": "520.00", "value": "525.00", ...}}
            if isinstance(pos_raw, dict):
                for pos_id, pos in pos_raw.items():
                    if not isinstance(pos, dict):
                        continue
                    kraken_pair = pos.get("pair", "")
                    agent_pair = self._kraken_to_agent_pair(kraken_pair)
                    pos_type = pos.get("type", "buy")
                    vol = float(pos.get("vol", 0))
                    cost = float(pos.get("cost", 0))
                    value = float(pos.get("value", 0))
                    entry_price = cost / vol if vol > 0 else 0.0
                    current_price = value / vol if vol > 0 else 0.0
                    unrealized = value - cost if pos_type == "buy" else cost - value
                    open_positions.append(Position(
                        pair=agent_pair,
                        direction=Direction.BUY if pos_type == "buy" else Direction.SELL,
                        size=vol,
                        entry_price=entry_price,
                        current_price=current_price,
                        unrealized_pnl=unrealized,
                        execution_layer=ExecutionLayer.KRAKEN,
                    ))
        except Exception as e:
            logger.debug("Could not fetch positions (may be empty): %s", e)

        # Daily stats from in-memory tracker
        today_start = self._today_start_ts()
        today_trades = [t for t in self._daily_trades if t["ts"] >= today_start]
        daily_pnl = sum(t.get("pnl", 0.0) for t in today_trades)

        return PortfolioState(
            total_balance_usd=usd_balance,
            available_balance_usd=usd_balance,
            open_positions=open_positions,
            daily_pnl_usd=daily_pnl,
            daily_trade_count=len(today_trades),
            last_trade_timestamp=self._last_trade_ts,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute_order(self, order: Order) -> ExecutionResult:
        """
        Place a market order via Kraken CLI.

        CLI command (example buy):
            kraken [--sandbox] order add \\
                --pair XBTUSD \\
                --type buy \\
                --ordertype market \\
                --volume 0.001

        Volume is derived from size_pct of available balance divided by
        current price. We fetch a quick ticker to get current price for
        volume calculation.
        """
        kraken_pair = _kraken_pair(order.pair)
        direction = order.direction.value  # "buy" or "sell"

        # Get current price + balance to compute volume
        try:
            md = await self.get_market_data(order.pair, num_candles=1)
            portfolio = await self.get_portfolio()
            current_price = md.current_price
            capital = portfolio.available_balance_usd
        except Exception as e:
            return ExecutionResult(
                success=False,
                trade_id=order.trade_id or str(uuid.uuid4())[:8],
                pair=order.pair,
                direction=order.direction,
                executed_price=0.0,
                executed_size=0.0,
                execution_layer=ExecutionLayer.KRAKEN,
                error=f"Pre-order data fetch failed: {e}",
            )

        trade_capital = capital * (order.size_pct / 100.0)
        volume = trade_capital / current_price if current_price > 0 else 0.0
        volume = round(volume, 8)

        if volume <= 0:
            return ExecutionResult(
                success=False,
                trade_id=order.trade_id or str(uuid.uuid4())[:8],
                pair=order.pair,
                direction=order.direction,
                executed_price=current_price,
                executed_size=0.0,
                execution_layer=ExecutionLayer.KRAKEN,
                error="Computed volume is zero — check balance and size_pct",
            )

        # Build CLI command
        sandbox_prefix = self._sandbox_args()
        cmd_args = (
            sandbox_prefix
            + ["order", "add"]
            + ["--pair", kraken_pair]
            + ["--type", direction]
            + ["--ordertype", order.order_type]
            + ["--volume", str(volume)]
        )
        if order.order_type == "limit" and order.limit_price:
            cmd_args += ["--price", str(order.limit_price)]

        trade_id = order.trade_id or str(uuid.uuid4())[:8]
        try:
            result_raw = await self._run(*cmd_args)
        except Exception as e:
            return ExecutionResult(
                success=False,
                trade_id=trade_id,
                pair=order.pair,
                direction=order.direction,
                executed_price=current_price,
                executed_size=0.0,
                execution_layer=ExecutionLayer.KRAKEN,
                error=str(e),
            )

        # Parse txid from response: {"descr": {...}, "txid": ["OXXXX-XXXXX-XXXXXX"]}
        txids = result_raw.get("txid", [])
        ext_trade_id = txids[0] if txids else trade_id

        # Track for daily stats
        self._last_trade_ts = int(time.time())
        self._daily_trades.append({"ts": self._last_trade_ts, "pnl": 0.0, "id": ext_trade_id})

        logger.info(
            "Order placed [%s] %s %s %.8f @ market (sandbox=%s) txid=%s",
            trade_id, direction, kraken_pair, volume, self._sandbox, ext_trade_id,
        )

        return ExecutionResult(
            success=True,
            trade_id=ext_trade_id,
            pair=order.pair,
            direction=order.direction,
            executed_price=current_price,
            executed_size=volume,
            execution_layer=ExecutionLayer.KRAKEN,
        )

    async def close_position(self, pair: str, sandbox: bool = True) -> ExecutionResult:
        """
        Close all open positions for a pair by placing an offsetting market order.

        CLI command:
            kraken [--sandbox] order add \\
                --pair XBTUSD --type sell --ordertype market --volume {open_vol}
        """
        kraken_pair = _kraken_pair(pair)

        # Get open position size
        try:
            portfolio = await self.get_portfolio()
            pos = next(
                (p for p in portfolio.open_positions if p.pair == pair),
                None,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                trade_id=str(uuid.uuid4())[:8],
                pair=pair,
                direction=Direction.SELL,
                executed_price=0.0,
                executed_size=0.0,
                execution_layer=ExecutionLayer.KRAKEN,
                error=f"Could not fetch portfolio for close: {e}",
            )

        if pos is None:
            return ExecutionResult(
                success=False,
                trade_id=str(uuid.uuid4())[:8],
                pair=pair,
                direction=Direction.SELL,
                executed_price=0.0,
                executed_size=0.0,
                execution_layer=ExecutionLayer.KRAKEN,
                error=f"No open position found for {pair}",
            )

        close_direction = "sell" if pos.direction == Direction.BUY else "buy"
        volume = round(pos.size, 8)

        sandbox_prefix = ["--sandbox"] if sandbox else []
        cmd_args = (
            sandbox_prefix
            + ["order", "add"]
            + ["--pair", kraken_pair]
            + ["--type", close_direction]
            + ["--ordertype", "market"]
            + ["--volume", str(volume)]
        )

        trade_id = str(uuid.uuid4())[:8]
        try:
            result_raw = await self._run(*cmd_args)
        except Exception as e:
            return ExecutionResult(
                success=False,
                trade_id=trade_id,
                pair=pair,
                direction=Direction.SELL,
                executed_price=pos.current_price,
                executed_size=0.0,
                execution_layer=ExecutionLayer.KRAKEN,
                error=str(e),
            )

        txids = result_raw.get("txid", [])
        ext_id = txids[0] if txids else trade_id
        self._last_trade_ts = int(time.time())

        return ExecutionResult(
            success=True,
            trade_id=ext_id,
            pair=pair,
            direction=Direction(close_direction),
            executed_price=pos.current_price,
            executed_size=volume,
            execution_layer=ExecutionLayer.KRAKEN,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Verify Kraken CLI is reachable by running a status/version check."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            version = stdout.decode().strip()
            logger.info("Kraken CLI ready: %s | sandbox=%s", version, self._sandbox)
        except FileNotFoundError:
            logger.warning(
                "Kraken CLI binary not found at '%s'. "
                "Market data and order execution will fail. "
                "Install: https://github.com/krakenfx/kraken-cli",
                self._cli,
            )
        except Exception as e:
            logger.warning("Kraken CLI connect check failed: %s", e)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_pair_data(data: dict, kraken_pair: str) -> Any:
        """
        Kraken sometimes uses different key variants for the same pair
        (e.g., XBTUSD vs XXBTZUSD). Try common variants.
        """
        if kraken_pair in data:
            return data[kraken_pair]
        # Try with X/Z prefix variants
        alt = f"X{kraken_pair[:-3]}Z{kraken_pair[-3:]}"
        if alt in data:
            return data[alt]
        # Return first non-"last" value if only one pair in result
        candidates = {k: v for k, v in data.items() if k != "last"}
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        return None

    @staticmethod
    def _kraken_to_agent_pair(kraken_pair: str) -> str:
        """Convert Kraken CLI pair to agent format (e.g. XBTUSD → BTC/USD)."""
        reverse = {v: k for k, v in _PAIR_MAP.items()}
        if kraken_pair in reverse:
            return reverse[kraken_pair]
        # Strip X/Z prefix variants
        stripped = kraken_pair.lstrip("X").replace("Z", "/", 1)
        return stripped if "/" in stripped else kraken_pair

    @staticmethod
    def _today_start_ts() -> int:
        """UTC midnight timestamp for today."""
        import datetime
        now = datetime.datetime.utcnow()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(midnight.timestamp())
