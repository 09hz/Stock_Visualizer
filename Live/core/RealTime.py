from __future__ import annotations

import asyncio
import csv
import queue
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd
from ib_async import IB, Stock, Ticker, util

from utils.chart_utils import apply_tick_to_bars, normalize_history_df, resample_bars


OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

TIMEFRAME_MAP: Dict[str, Tuple[str, str]] = {
    "1 min": ("1 min", "1 D"),
    "5 min": ("5 mins", "2 D"),
    "15 min": ("15 mins", "5 D"),
    "1 hour": ("1 hour", "30 D"),
    "1 day": ("1 day", "1 Y"),
}


@dataclass
class SymbolState:
    symbol: str
    timeframe: str
    bars: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=OHLCV_COLUMNS)
    )
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    last_size: float = 0.0
    updated_at: Optional[datetime] = None
    tick_count: int = 0


class RealTimeIB:
    """
    Thread-safe Interactive Brokers realtime/historical data adapter.

    Important design rule:
    All ib_async / IB API calls must run on the dedicated IB thread. Dash
    callbacks run on request threads, so public historical/live methods route
    work through _run_on_ib_thread(...) when the IB loop is running.

    This fixes the common replay/date-range failure where historical requests
    silently fail or hang because reqHistoricalData is called from the wrong
    thread/event loop.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4001,
        client_id: Optional[int] = None,
    ):
        self.host = host
        self.port = port
        self.client_id = (
            client_id if client_id is not None else random.randint(1000, 999999)
        )

        self.ib = IB()

        self._contracts: Dict[str, Stock] = {}
        self._tickers: Dict[str, Ticker] = {}
        self._states: Dict[Tuple[str, str], SymbolState] = {}

        self._lock = threading.RLock()
        self._runner_thread: Optional[threading.Thread] = None
        self._ib_thread_id: Optional[int] = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._startup_error: Optional[str] = None

        # Non-blocking symbol subscription requests from Dash callbacks.
        self._requests: queue.Queue[tuple[Any, ...]] = queue.Queue()

        # Blocking call queue for operations that must execute on the IB thread.
        self._call_queue: queue.Queue[
            tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any], queue.Queue]
        ] = queue.Queue()

        project_root = Path(__file__).resolve().parent.parent
        data_dir = project_root / "data"

        self.nasdaq_file = data_dir / "nasdaq_tickers_simple.txt"
        self.nasdaq_symbols = self._load_nasdaq_symbols(self.nasdaq_file)

        self.company_file = data_dir / "nasdaq_symbol_names_filled.csv"
        self.company_names = self._load_company_names(self.company_file)

    # ------------------------------------------------------------------
    # Static/reference data
    # ------------------------------------------------------------------
    def _load_nasdaq_symbols(self, file_path: Path) -> set[str]:
        if not file_path.exists():
            print(f"[WARN] NASDAQ file not found: {file_path}", flush=True)
            return set()

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return {line.strip().upper() for line in f if line.strip()}
        except Exception as exc:
            print(f"[WARN] Could not read NASDAQ symbols {file_path}: {exc}", flush=True)
            return set()

    def _load_company_names(self, file_path: Path) -> dict[str, str]:
        if not file_path.exists():
            print(f"[WARN] Company file not found: {file_path}", flush=True)
            return {}

        company_map: dict[str, str] = {}

        try:
            with open(file_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)

                for row in reader:
                    symbol = (row.get("symbol") or "").strip().upper()
                    name = (row.get("name") or "").strip()

                    if symbol:
                        company_map[symbol] = name
        except Exception as exc:
            print(f"[WARN] Could not read company names {file_path}: {exc}", flush=True)

        return company_map

    def is_valid_nasdaq_symbol(self, symbol: str) -> bool:
        symbol = self._sanitize_symbol(symbol)

        # If the local ticker file is missing/empty, do not brick the app.
        if not self.nasdaq_symbols:
            return True

        return symbol in self.nasdaq_symbols

    def get_company_name(self, symbol: str) -> str:
        symbol = self._sanitize_symbol(symbol)
        return self.company_names.get(symbol) or symbol

    def get_symbol_options(self) -> list[dict[str, object]]:
        options: list[dict[str, object]] = []

        for symbol in sorted(self.nasdaq_symbols):
            company = self.company_names.get(symbol, "")
            label_text = f"{symbol} - {company}" if company else symbol
            search_text = f"{symbol} {company}".strip()

            options.append(
                {
                    "label": label_text,
                    "value": symbol,
                    "search": search_text,
                }
            )

        return options

    # ------------------------------------------------------------------
    # IB thread lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """
        Connect on the current thread.

        In normal app usage, start(...) creates the IB thread and connect() is
        called inside that thread. Public callers generally should call start().
        """
        if not self.ib.isConnected():
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                timeout=30,
            )

    def disconnect(self) -> None:
        self._stop.set()

        if self._runner_thread and self._runner_thread.is_alive():
            try:
                self._run_on_ib_thread(self._disconnect_ib_thread, timeout=10)
            except Exception as exc:
                print(f"[IB DISCONNECT WARNING] {exc}", flush=True)
            return

        self._disconnect_ib_thread()

    def _disconnect_ib_thread(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def start(self, symbol: str, timeframe: str = "1 min") -> None:
        """
        Start the dedicated IB thread and make the first symbol ready.

        Calling start again while the thread is alive is safe; it queues the new
        symbol instead of creating a second IB connection.
        """
        symbol = self._sanitize_symbol(symbol)

        if self._runner_thread and self._runner_thread.is_alive():
            self.request_symbol(symbol)
            return

        self._ready.clear()
        self._stop.clear()
        self._startup_error = None

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._ib_thread_id = threading.get_ident()

            try:
                self.connect()
                self._ensure_symbol_ready_ib_thread(symbol, timeframe)
                self._ready.set()

                while not self._stop.is_set():
                    self._process_thread_calls()
                    self._process_requests()
                    self.ib.sleep(0.10)

            except Exception as exc:
                self._startup_error = str(exc)
                self._ready.set()
                print(f"[IB LOOP ERROR] {exc}", flush=True)

            finally:
                try:
                    self._disconnect_ib_thread()
                except Exception:
                    pass

                try:
                    loop.close()
                except Exception:
                    pass

                self._ib_thread_id = None

        self._runner_thread = threading.Thread(
            target=_run,
            name="RealTimeIBThread",
            daemon=True,
        )
        self._runner_thread.start()
        self._ready.wait(timeout=30)

        if self._startup_error:
            raise RuntimeError(self._startup_error)

        if not self._ready.is_set():
            raise TimeoutError("Timed out waiting for IB thread to start.")

    def _is_ib_thread(self) -> bool:
        return (
            self._ib_thread_id is not None
            and threading.get_ident() == self._ib_thread_id
        )

    def _run_on_ib_thread(
        self,
        func: Callable[..., Any],
        *args: Any,
        timeout: float = 90,
        **kwargs: Any,
    ) -> Any:
        """
        Execute func on the IB thread and return its result.

        If called from the IB thread, execute directly.
        If the IB thread is not running yet, execute directly as a fallback.
        """
        if self._is_ib_thread():
            return func(*args, **kwargs)

        if not (self._runner_thread and self._runner_thread.is_alive()):
            return func(*args, **kwargs)

        result_queue: queue.Queue = queue.Queue(maxsize=1)
        self._call_queue.put((func, args, kwargs, result_queue))

        try:
            status, payload = result_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                f"Timed out waiting for IB-thread call: {getattr(func, '__name__', func)}"
            ) from exc

        if status == "ok":
            return payload

        raise payload

    def _process_thread_calls(self) -> None:
        while True:
            try:
                func, args, kwargs, result_queue = self._call_queue.get_nowait()
            except queue.Empty:
                break

            try:
                result = func(*args, **kwargs)
                result_queue.put(("ok", result))
            except Exception as exc:
                result_queue.put(("err", exc))

    def request_symbol(self, symbol: str) -> None:
        symbol = self._sanitize_symbol(symbol)
        self._requests.put(("symbol", symbol))

    def _process_requests(self) -> None:
        while True:
            try:
                req = self._requests.get_nowait()
            except queue.Empty:
                break

            try:
                kind = req[0]

                if kind == "symbol":
                    symbol = str(req[1])
                    self._ensure_symbol_ready_ib_thread(symbol, "1 min")

            except Exception as exc:
                print(f"[REQUEST ERROR] {req}: {exc}", flush=True)

    # ------------------------------------------------------------------
    # Contract and historical data
    # ------------------------------------------------------------------
    def get_contract(self, symbol: str) -> Stock:
        return self._run_on_ib_thread(self._get_contract_ib_thread, symbol)

    def _get_contract_ib_thread(self, symbol: str) -> Stock:
        symbol = self._sanitize_symbol(symbol)

        if not self.is_valid_nasdaq_symbol(symbol):
            raise ValueError(f"{symbol} is not in NASDAQ symbol list")

        with self._lock:
            cached = self._contracts.get(symbol)
            if cached is not None:
                return cached

        contract = Stock(symbol, "SMART", "USD", primaryExchange="NASDAQ")
        qualified = self.ib.qualifyContracts(contract)

        if qualified:
            contract = qualified[0]

        with self._lock:
            self._contracts[symbol] = contract

        return contract

    def _empty_bars(self) -> pd.DataFrame:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    def _normalize_ib_bars(self, bars: Any) -> pd.DataFrame:
        if not bars:
            return self._empty_bars()

        df = util.df(bars)

        if df is None or df.empty:
            return self._empty_bars()

        df = normalize_history_df(df)

        if df is None or df.empty:
            return self._empty_bars()

        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], errors="coerce", format="mixed")
            df = df.dropna(subset=["time"]).copy()

        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" not in df.columns:
            df["volume"] = 0

        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        df = df.dropna(subset=["open", "high", "low", "close"]).copy()

        if df.empty:
            return self._empty_bars()

        return df[OHLCV_COLUMNS].sort_values("time").reset_index(drop=True)

    def load_history(self, symbol: str, timeframe: str) -> pd.DataFrame:
        return self._run_on_ib_thread(
            self._load_history_ib_thread,
            symbol,
            timeframe,
            timeout=90,
        )

    def _load_history_ib_thread(self, symbol: str, timeframe: str) -> pd.DataFrame:
        symbol = self._sanitize_symbol(symbol)

        if timeframe not in TIMEFRAME_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        contract = self._get_contract_ib_thread(symbol)
        bar_size, duration = TIMEFRAME_MAP[timeframe]

        print(
            f"[IB HISTORY SEND] {symbol} timeframe={timeframe} "
            f"bar_size={bar_size} duration={duration}",
            flush=True,
        )

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )

        df = self._normalize_ib_bars(bars)

        with self._lock:
            key = (symbol, timeframe)
            state = self._states.get(
                key,
                SymbolState(symbol=symbol, timeframe=timeframe),
            )
            state.bars = df.copy()
            state.updated_at = datetime.now()

            if not df.empty:
                state.last = float(df.iloc[-1]["close"])

            self._states[key] = state

        print(f"[IB HISTORY RETURNED] {symbol} rows={len(df)}", flush=True)
        return df

    def load_history_at(
        self,
        symbol: str,
        timeframe: str,
        end_dt: datetime,
    ) -> pd.DataFrame:
        return self._run_on_ib_thread(
            self._load_history_at_ib_thread,
            symbol,
            timeframe,
            end_dt,
            timeout=90,
        )

    def _load_history_at_ib_thread(
        self,
        symbol: str,
        timeframe: str,
        end_dt: datetime,
    ) -> pd.DataFrame:
        symbol = self._sanitize_symbol(symbol)

        if timeframe not in TIMEFRAME_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        contract = self._get_contract_ib_thread(symbol)
        bar_size, duration = TIMEFRAME_MAP[timeframe]

        if not isinstance(end_dt, datetime):
            end_dt = pd.to_datetime(end_dt, errors="coerce").to_pydatetime()

        end_text = end_dt.strftime("%Y%m%d %H:%M:%S")

        print(
            f"[IB HISTORY_AT SEND] {symbol} timeframe={timeframe} "
            f"end={end_text} bar_size={bar_size} duration={duration}",
            flush=True,
        )

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=end_text,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )

        df = self._normalize_ib_bars(bars)
        print(f"[IB HISTORY_AT RETURNED] {symbol} rows={len(df)}", flush=True)
        return df

    def load_history_range(
        self,
        symbol: str,
        timeframe: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        return self._run_on_ib_thread(
            self._load_history_range_ib_thread,
            symbol,
            timeframe,
            start_dt,
            end_dt,
            timeout=180,
        )

    def _load_history_range_ib_thread(
        self,
        symbol: str,
        timeframe: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        symbol = self._sanitize_symbol(symbol)

        start_dt = pd.to_datetime(start_dt, errors="coerce")
        end_dt = pd.to_datetime(end_dt, errors="coerce")

        if pd.isna(start_dt) or pd.isna(end_dt):
            raise ValueError("Invalid start_dt or end_dt")

        start_dt = start_dt.to_pydatetime()
        end_dt = end_dt.to_pydatetime()

        if start_dt >= end_dt:
            raise ValueError("start_dt must be before end_dt")

        if timeframe not in TIMEFRAME_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        print(
            f"[REALTIME load_history_range ENTERED] "
            f"symbol={symbol}, timeframe={timeframe}, start={start_dt}, end={end_dt}",
            flush=True,
        )

        pieces: list[pd.DataFrame] = []
        cursor = end_dt
        seen_oldest: set[str] = set()

        # Prevent runaway loops if IB returns overlapping chunks.
        max_chunks = {
            "1 min": 20,
            "5 mins": 20,
            "15 mins": 20,
            "1 hour": 24,
            "1 day": 20,
        }.get(timeframe, 20)

        for chunk_number in range(1, max_chunks + 1):
            if cursor <= start_dt:
                break

            print(
                f"[LHR CHUNK START] {symbol} chunk={chunk_number} cursor={cursor}",
                flush=True,
            )

            chunk = self._load_history_at_ib_thread(symbol, timeframe, cursor)

            if chunk is None or chunk.empty:
                print(f"[LHR CHUNK EMPTY] {symbol} cursor={cursor}", flush=True)
                break

            chunk = chunk.copy()
            chunk["time"] = pd.to_datetime(chunk["time"], errors="coerce", format="mixed")
            chunk = chunk.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)

            if chunk.empty:
                print(f"[LHR CHUNK INVALID] {symbol} cursor={cursor}", flush=True)
                break

            oldest = chunk["time"].min().to_pydatetime()
            newest = chunk["time"].max().to_pydatetime()

            print(
                f"[LHR CHUNK DONE] {symbol} rows={len(chunk)} "
                f"oldest={oldest} newest={newest}",
                flush=True,
            )

            pieces.append(chunk)

            if oldest <= start_dt:
                break

            oldest_key = oldest.isoformat()
            if oldest_key in seen_oldest:
                print(
                    f"[LHR STOP] {symbol} repeated oldest timestamp {oldest_key}",
                    flush=True,
                )
                break

            seen_oldest.add(oldest_key)
            cursor = oldest - timedelta(seconds=1)
            self.ib.sleep(0.25)

        if not pieces:
            return self._empty_bars()

        out = pd.concat(pieces, ignore_index=True)
        out["time"] = pd.to_datetime(out["time"], errors="coerce", format="mixed")
        out = out.dropna(subset=["time"]).copy()
        out = out.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)

        out = out[(out["time"] >= start_dt) & (out["time"] <= end_dt)].reset_index(drop=True)

        if out.empty:
            return self._empty_bars()

        out = out[OHLCV_COLUMNS].copy()

        print(
            f"[LHR COMPLETE] {symbol} start={start_dt} end={end_dt} rows={len(out)}",
            flush=True,
        )

        return out

    # ------------------------------------------------------------------
    # Live market data
    # ------------------------------------------------------------------
    def subscribe_live(self, symbol: str, timeframe: str = "1 min") -> None:
        self._run_on_ib_thread(
            self._subscribe_live_ib_thread,
            symbol,
            timeframe,
            timeout=90,
        )

    def _subscribe_live_ib_thread(self, symbol: str, timeframe: str = "1 min") -> None:
        symbol = self._sanitize_symbol(symbol)
        timeframe = "1 min"

        contract = self._get_contract_ib_thread(symbol)

        with self._lock:
            key = (symbol, timeframe)
            has_state = key in self._states and not self._states[key].bars.empty

            if symbol in self._tickers:
                return

        if not has_state:
            self._load_history_ib_thread(symbol, timeframe)

        ticker = self.ib.reqMktData(contract, "", False, False)
        ticker.updateEvent += self._make_tick_handler(symbol, timeframe)

        with self._lock:
            self._tickers[symbol] = ticker

        print(f"[IB LIVE SUBSCRIBED] {symbol}", flush=True)

    def _make_tick_handler(self, symbol: str, timeframe: str):
        def on_tick(ticker: Ticker, *args: Any) -> None:
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

                try:
                    state.bars = apply_tick_to_bars(
                        state.bars,
                        price=price,
                        size=size,
                        tick_time=datetime.now(),
                    )
                except Exception as exc:
                    print(f"[TICK BAR PATCH ERROR] {symbol}: {exc}", flush=True)

                self._states[key] = state

        return on_tick

    def get_snapshot(self, symbol: str, timeframe: str) -> SymbolState:
        """
        Return a copy of the latest in-memory bars and quote state.

        This method intentionally does not call IB directly, so it is safe from
        Dash callbacks and fast render loops.
        """
        symbol = self._sanitize_symbol(symbol)
        timeframe = timeframe or "1 min"
        key = (symbol, "1 min")

        with self._lock:
            state = self._states.get(key)

            if state is None:
                raise ValueError(f"No loaded state for {symbol} 1 min")

            bars = state.bars.copy()
            bid = state.bid
            ask = state.ask
            last = state.last
            last_size = state.last_size
            updated_at = state.updated_at
            tick_count = state.tick_count

            if last is not None:
                try:
                    bars = apply_tick_to_bars(
                        bars,
                        price=float(last),
                        size=float(last_size or 0),
                        tick_time=datetime.now(),
                    )

                    state.bars = bars.copy()
                    self._states[key] = state

                except Exception as exc:
                    print(f"[SNAPSHOT BAR PATCH ERROR] {symbol}: {exc}", flush=True)

        if timeframe != "1 min":
            bars = resample_bars(bars, timeframe)

        return SymbolState(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
            bid=bid,
            ask=ask,
            last=last,
            last_size=last_size,
            updated_at=updated_at,
            tick_count=tick_count,
        )

    def ensure_symbol_ready(self, symbol: str, timeframe: str = "1 min") -> None:
        self._run_on_ib_thread(
            self._ensure_symbol_ready_ib_thread,
            symbol,
            timeframe,
            timeout=90,
        )

    def _ensure_symbol_ready_ib_thread(
        self,
        symbol: str,
        timeframe: str = "1 min",
    ) -> None:
        symbol = self._sanitize_symbol(symbol)
        timeframe = "1 min"

        self._load_history_ib_thread(symbol, timeframe)
        self._subscribe_live_ib_thread(symbol, timeframe)

    @staticmethod
    def _sanitize_symbol(symbol: str) -> str:
        cleaned = "".join(
            ch
            for ch in str(symbol or "").upper().strip()
            if ch.isalnum() or ch in {".", "-"}
        )

        if not cleaned:
            raise ValueError("Invalid symbol.")

        return cleaned
