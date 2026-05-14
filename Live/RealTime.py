from __future__ import annotations

import asyncio
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple

import pandas as pd
from ib_async import IB, Stock, Ticker, util

from chart_utils import apply_tick_to_bars, normalize_history_df


TIMEFRAME_MAP: Dict[str, Tuple[str, str]] = {
    "1 min": ("1 min", "1 D"),
    "5 mins": ("5 mins", "2 D"),
    "15 mins": ("15 mins", "5 D"),
    "1 hour": ("1 hour", "30 D"),
    "1 day": ("1 day", "1 Y"),
}


@dataclass
class SymbolState:
    symbol: str
    timeframe: str
    bars: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["time", "open", "high", "low", "close", "volume"]
    ))
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    last_size: float = 0.0
    updated_at: Optional[datetime] = None
    tick_count: int = 0


class RealTimeIB:
    def __init__(self, host: str = "127.0.0.1", port: int = 4001, client_id: Optional[int] = None):
        self.host = host
        self.port = port
        self.client_id = client_id if client_id is not None else random.randint(1000, 999999)

        self.ib = IB()
        self._contracts: Dict[str, Stock] = {}
        self._tickers: Dict[str, Ticker] = {}
        self._states: Dict[Tuple[str, str], SymbolState] = {}
        self._lock = threading.RLock()
        self._runner_thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._startup_error: Optional[str] = None

    def connect(self) -> None:
        if not self.ib.isConnected():
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=30)

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def start(self, symbol: str, timeframe: str) -> None:
        if self._runner_thread and self._runner_thread.is_alive():
            return

        symbol = self._sanitize_symbol(symbol)

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                self.connect()
                self.ensure_symbol_ready(symbol, timeframe)
                self._ready.set()
                self.ib.run()
            except Exception as exc:
                self._startup_error = str(exc)
                self._ready.set()
                print(f"[IB LOOP ERROR] {exc}", flush=True)

        self._runner_thread = threading.Thread(target=_run, daemon=True)
        self._runner_thread.start()
        self._ready.wait(timeout=15)
        if self._startup_error:
            raise RuntimeError(self._startup_error)

    def get_contract(self, symbol: str) -> Stock:
        symbol = self._sanitize_symbol(symbol)
        with self._lock:
            if symbol in self._contracts:
                return self._contracts[symbol]

        contract = Stock(symbol, "SMART", "USD", primaryExchange="NASDAQ")
        self.ib.qualifyContracts(contract)

        with self._lock:
            self._contracts[symbol] = contract

        return contract

    def load_history(self, symbol: str, timeframe: str) -> pd.DataFrame:
        symbol = self._sanitize_symbol(symbol)
        if timeframe not in TIMEFRAME_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        contract = self.get_contract(symbol)
        bar_size, duration = TIMEFRAME_MAP[timeframe]

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )

        df = util.df(bars)
        df = normalize_history_df(df)

        with self._lock:
            key = (symbol, timeframe)
            state = self._states.get(key, SymbolState(symbol=symbol, timeframe=timeframe))
            state.bars = df
            state.updated_at = datetime.now()
            self._states[key] = state

        return df

    def subscribe_live(self, symbol: str, timeframe: str = "1 min") -> None:
        symbol = self._sanitize_symbol(symbol)
        contract = self.get_contract(symbol)

        with self._lock:
            key = (symbol, timeframe)
            has_state = key in self._states and not self._states[key].bars.empty
            if symbol in self._tickers:
                return

        if not has_state:
            self.load_history(symbol, timeframe)

        ticker = self.ib.reqMktData(contract, "", False, False)
        ticker.updateEvent += self._make_tick_handler(symbol, timeframe)

        with self._lock:
            self._tickers[symbol] = ticker

    def _make_tick_handler(self, symbol: str, timeframe: str):
        def on_tick(ticker: Ticker, *args):
            price_raw = ticker.last if ticker.last is not None else ticker.marketPrice()
            if price_raw is None or pd.isna(price_raw):
                return

            price = float(price_raw)
            size = float(ticker.lastSize or 0)

            with self._lock:
                key = (symbol, timeframe)
                state = self._states.get(key)
                if state is None:
                    return

                state.bid = float(ticker.bid) if ticker.bid is not None else state.bid
                state.ask = float(ticker.ask) if ticker.ask is not None else state.ask
                state.last = price
                state.last_size = size
                state.updated_at = datetime.now()
                state.tick_count += 1
                state.bars = apply_tick_to_bars(
                    state.bars,
                    price=price,
                    size=size,
                    tick_time=datetime.now(),
                )
                self._states[key] = state

        return on_tick

    def get_snapshot(self, symbol: str, timeframe: str) -> SymbolState:
        symbol = self._sanitize_symbol(symbol)
        key = (symbol, timeframe)

        with self._lock:
            state = self._states.get(key)

        if state is None:
            raise ValueError(f"No loaded state for {symbol} {timeframe}")

        return SymbolState(
            symbol=state.symbol,
            timeframe=state.timeframe,
            bars=state.bars.copy(),
            bid=state.bid,
            ask=state.ask,
            last=state.last,
            last_size=state.last_size,
            updated_at=state.updated_at,
            tick_count=state.tick_count,
        )

    def ensure_symbol_ready(self, symbol: str, timeframe: str) -> None:
        self.load_history(symbol, timeframe)
        self.subscribe_live(symbol, timeframe)

    @staticmethod
    def _sanitize_symbol(symbol: str) -> str:
        cleaned = "".join(ch for ch in symbol.upper().strip() if ch.isalnum() or ch in {".", "-"})
        if not cleaned:
            raise ValueError("Invalid symbol.")
        return cleaned