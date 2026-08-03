from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock, RLock
from typing import TYPE_CHECKING, Optional

import pandas as pd

from core.ReplayModule import ReplayEngine
from services.bar_store import BarStore
from services.market_calendar_service import MarketCalendarService


if TYPE_CHECKING:
    from core.RealTime import RealTimeIB


OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class ReplayLoadCancelled(RuntimeError):
    """Raised when a newer request supersedes an in-progress replay load."""


class ReplayService:
    """
    Replay data coordinator.

    Fast path:
        validated memory cache -> validated disk cache -> already-loaded live bars
        -> IB historical request

    Important:
        The replay engine should only play already-loaded bars. It should never
        request IB data during playback.

    v1H cache guard:
        Historical replay-date cache is no longer accepted just because it
        exists. A previous bad partial cache, for example 12 bars under a
        ("MSFT", "1 min", "2026-06-18") key, is invalidated and refreshed.
    """

    # Keep replay datasets responsive while retaining the finest useful source
    # interval for the selected date range. Display intervals may resample this
    # source upward, but must never pretend to create finer bars from it.
    MAX_RANGE_SOURCE_BARS = 5_000
    RANGE_SOURCE_CANDIDATES = (
        ("1 min", 390),
        ("5 min", 78),
        ("15 min", 26),
        ("1 hour", 7),
        ("1 day", 1),
    )

    @classmethod
    def normalize_range_timeframe(cls, timeframe: str | None) -> str:
        aliases = {
            "1m": "1 min",
            "1 minute": "1 min",
            "1min": "1 min",
            "5m": "5 min",
            "5 mins": "5 min",
            "15m": "15 min",
            "15 mins": "15 min",
            "1h": "1 hour",
            "1d": "1 day",
            "1 day bars": "1 day",
        }
        value = str(timeframe or "1 min").lower().strip()
        return aliases.get(value, value)

    @classmethod
    def estimate_range_bar_count(
        cls,
        timeframe: str | None,
        trading_day_count: int,
    ) -> int:
        """Conservatively estimate regular-session candles for validation."""

        normalized = cls.normalize_range_timeframe(timeframe)
        bars_per_day = dict(cls.RANGE_SOURCE_CANDIDATES).get(normalized)
        if bars_per_day is None:
            raise ValueError(f"Unsupported replay timeframe: {timeframe}")

        return max(1, int(trading_day_count or 1)) * bars_per_day

    @classmethod
    def max_range_days_for_timeframe(cls, timeframe: str | None) -> int:
        normalized = cls.normalize_range_timeframe(timeframe)
        bars_per_day = dict(cls.RANGE_SOURCE_CANDIDATES).get(normalized)
        if bars_per_day is None:
            raise ValueError(f"Unsupported replay timeframe: {timeframe}")
        return max(1, cls.MAX_RANGE_SOURCE_BARS // bars_per_day)

    @classmethod
    def choose_range_source_timeframe(
        cls,
        trading_day_count: int,
        max_bars: int | None = None,
    ) -> str:
        """Return the finest source interval that fits the replay bar budget."""

        day_count = max(1, int(trading_day_count or 1))
        bar_budget = max(1, int(max_bars or cls.MAX_RANGE_SOURCE_BARS))

        for timeframe, bars_per_day in cls.RANGE_SOURCE_CANDIDATES:
            if day_count * bars_per_day <= bar_budget:
                return timeframe

        return "1 day"

    def __init__(
        self,
        rt: "RealTimeIB",
        engine: ReplayEngine,
        bar_store: Optional[BarStore] = None,
        market_calendar: Optional[MarketCalendarService] = None,
    ):
        self.rt = rt
        self.engine = engine
        self.bar_store = bar_store or BarStore()
        self.market_calendar = market_calendar or MarketCalendarService()
        self.memory_cache: dict[tuple[str, str, str], pd.DataFrame] = {}

        self.current_symbol: Optional[str] = None
        self.current_timeframe: str = "1 min"
        self.current_replay_date: Optional[str] = None
        self.current_replay_end_date: Optional[str] = None
        self._load_request_lock = RLock()
        self._active_load_request_id: Optional[str] = None
        self._load_progress_lock = Lock()
        self._load_progress = {
            "active": False,
            "status": "idle",
            "completed_days": 0,
            "total_days": 0,
            "bars_loaded": 0,
            "source_timeframe": "1 min",
            "message": "Waiting to load replay data.",
            "started_at": None,
            "updated_at": None,
        }

    def claim_load_request(self, request_id: object) -> str:
        """Make request_id the sole replay load allowed to update this session."""
        normalized = str(request_id)
        with self._load_request_lock:
            self._active_load_request_id = normalized
        return normalized

    def is_load_request_current(self, request_id: object) -> bool:
        if request_id is None:
            return True
        with self._load_request_lock:
            return self._active_load_request_id == str(request_id)

    def _require_current_load_request(self, request_id: object) -> None:
        if not self.is_load_request_current(request_id):
            raise ReplayLoadCancelled(
                "Replay load was replaced by a newer request."
            )

    def begin_load_progress(
        self,
        total_days: int,
        source_timeframe: str,
        request_id: object = None,
    ) -> None:
        now = datetime.now()
        with self._load_request_lock:
            self._require_current_load_request(request_id)
            with self._load_progress_lock:
                self._load_progress = {
                    "active": True,
                    "status": "loading",
                    "completed_days": 0,
                    "total_days": max(1, int(total_days or 1)),
                    "bars_loaded": 0,
                    "source_timeframe": str(source_timeframe or "1 min"),
                    "message": "Preparing replay request...",
                    "started_at": now,
                    "updated_at": now,
                }

    def update_load_progress(
        self,
        *,
        completed_days: int,
        bars_loaded: int,
        message: str,
        request_id: object = None,
    ) -> None:
        with self._load_request_lock:
            self._require_current_load_request(request_id)
            with self._load_progress_lock:
                self._load_progress.update(
                    {
                        "completed_days": max(0, int(completed_days or 0)),
                        "bars_loaded": max(0, int(bars_loaded or 0)),
                        "message": str(message or "Loading replay data..."),
                        "updated_at": datetime.now(),
                    }
                )

    def finish_load_progress(
        self,
        bars_loaded: int,
        message: str,
        request_id: object = None,
    ) -> None:
        with self._load_request_lock:
            self._require_current_load_request(request_id)
            with self._load_progress_lock:
                total_days = max(1, int(self._load_progress.get("total_days", 1)))
                self._load_progress.update(
                    {
                        "active": False,
                        "status": "complete",
                        "completed_days": total_days,
                        "bars_loaded": max(0, int(bars_loaded or 0)),
                        "message": str(message or "Replay load complete."),
                        "updated_at": datetime.now(),
                    }
                )

    def fail_load_progress(self, message: str, request_id: object = None) -> None:
        with self._load_request_lock:
            self._require_current_load_request(request_id)
            with self._load_progress_lock:
                self._load_progress.update(
                    {
                        "active": False,
                        "status": "error",
                        "message": str(message or "Replay load failed."),
                        "updated_at": datetime.now(),
                    }
                )

    def get_load_progress(self) -> dict:
        with self._load_progress_lock:
            progress = dict(self._load_progress)

        started_at = progress.pop("started_at", None)
        progress.pop("updated_at", None)
        progress["elapsed_seconds"] = (
            max(0, int((datetime.now() - started_at).total_seconds()))
            if isinstance(started_at, datetime)
            else 0
        )
        return progress

    def _install_replay_dataset(
        self,
        bars: pd.DataFrame | None,
        *,
        symbol: str,
        timeframe: str,
        replay_date: Optional[str],
        replay_end_date: Optional[str],
        speed: Optional[float],
        request_id: object,
    ) -> None:
        """Atomically reject stale requests or install their replay dataset."""
        with self._load_request_lock:
            self._require_current_load_request(request_id)
            self.current_symbol = symbol
            self.current_timeframe = timeframe
            self.current_replay_date = replay_date
            self.current_replay_end_date = replay_end_date
            self.engine.reset()
            if bars is not None and not bars.empty:
                self.engine.load_from_df(bars)
            if speed is not None:
                self.engine.set_speed(speed)

    # ------------------------------------------------------------------
    # Cache keys / cache controls
    # ------------------------------------------------------------------
    def _make_cache_key(
        self,
        symbol: str,
        timeframe: str,
        replay_date: Optional[str],
    ) -> tuple[str, str, str]:
        symbol = self.rt._sanitize_symbol(symbol)
        timeframe = timeframe or "1 min"
        date_key = replay_date or "latest"
        return symbol, timeframe, date_key

    def clear_memory_cache(self) -> None:
        self.memory_cache.clear()

    def clear_disk_cache(self) -> None:
        self.bar_store.clear_all()

    def clear_cache(self) -> None:
        self.clear_memory_cache()

    def clear_symbol_cache(self, symbol: str) -> None:
        symbol = self.rt._sanitize_symbol(symbol)

        for key in [key for key in self.memory_cache if key[0] == symbol]:
            del self.memory_cache[key]

        self.bar_store.delete(symbol)

    # ------------------------------------------------------------------
    # Bar cleaning / replay-session validation
    # ------------------------------------------------------------------
    def _normalize_replay_bars(self, bars: pd.DataFrame | None) -> pd.DataFrame:
        if bars is None or bars.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        df = bars.copy()

        if "time" not in df.columns:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        df["time"] = pd.to_datetime(df["time"], errors="coerce", format="mixed")

        for col in ["open", "high", "low", "close"]:
            if col not in df.columns:
                df[col] = pd.NA
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" not in df.columns:
            df["volume"] = 0

        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

        df = df.dropna(subset=["time", "open", "high", "low", "close"]).copy()

        if df.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        df = (
            df[OHLCV_COLUMNS]
            .sort_values("time")
            .drop_duplicates(subset=["time"])
            .reset_index(drop=True)
        )

        return df

    def _session_bounds_for_date(self, replay_date) -> tuple[pd.Timestamp, pd.Timestamp]:
        selected = replay_date or self.market_calendar.last_completed_session()
        session_start, session_end = self.market_calendar.session_bounds(selected)

        # IB historical bars are normalized to exchange-local, timezone-naive
        # timestamps throughout this project.
        return (
            pd.Timestamp(session_start).tz_localize(None),
            pd.Timestamp(session_end).tz_localize(None),
        )

    def _filter_regular_session(self, bars: pd.DataFrame | None, replay_date) -> pd.DataFrame:
        df = self._normalize_replay_bars(bars)

        if df.empty:
            return df

        session_start, session_end = self._session_bounds_for_date(replay_date)

        return (
            df[(df["time"] >= session_start) & (df["time"] <= session_end)]
            .copy()
            .reset_index(drop=True)
        )

    def _is_historical_replay_date(self, replay_date) -> bool:
        selected = pd.to_datetime(replay_date, errors="coerce")

        if pd.isna(selected):
            return False

        return selected.date() < self.market_calendar.exchange_now().date()

    def _should_validate_full_session(
        self,
        replay_date: Optional[str],
        timeframe: str,
    ) -> bool:
        if not replay_date:
            return False

        tf = str(timeframe or "1 min").strip().lower()
        is_one_min = tf in {"1 min", "1m", "1 minute", "1min"}

        return is_one_min and self._is_historical_replay_date(replay_date)

    def _cache_is_complete_for_replay_day(
        self,
        bars: pd.DataFrame | None,
        replay_date,
        *,
        min_rows: int | None = None,
    ) -> tuple[bool, str]:
        """
        Validate cached one-minute replay data for a historical regular session.

        Returns:
            (True, reason) if the cache is safe to use
            (False, reason) if it should be invalidated/refetched
        """
        df = self._filter_regular_session(bars, replay_date)

        if df.empty:
            return False, "empty regular-session cache"

        session_start, session_end = self._session_bounds_for_date(replay_date)

        if min_rows is None:
            scheduled_minutes = max(
                1,
                int((session_end - session_start).total_seconds() // 60),
            )
            min_rows = max(1, scheduled_minutes - 30)
        min_rows = int(min_rows)

        first_time = df["time"].min()
        last_time = df["time"].max()
        row_count = int(len(df))

        has_enough_rows = row_count >= min_rows
        starts_near_open = first_time <= session_start + pd.Timedelta(minutes=5)
        # IB commonly returns 15:59 as the last one-minute bar for a 16:00 close.
        ends_near_close = last_time >= session_end - pd.Timedelta(minutes=5)

        if not has_enough_rows:
            return False, f"too few rows: {row_count}"

        if not starts_near_open:
            return False, f"starts too late: {first_time}"

        if not ends_near_close:
            return False, f"ends too early: {last_time}"

        return (
            True,
            f"complete rows={row_count} first={first_time} last={last_time}",
        )

    def _prepare_history_for_replay_date(
        self,
        bars: pd.DataFrame | None,
        replay_date: Optional[str],
    ) -> pd.DataFrame:
        if replay_date:
            return self._filter_regular_session(bars, replay_date)
        return self._normalize_replay_bars(bars)

    # ------------------------------------------------------------------
    # Data source loading
    # ------------------------------------------------------------------
    def _load_from_rt_or_ib(
        self,
        symbol: str,
        timeframe: str,
        replay_date: Optional[str],
    ) -> pd.DataFrame:
        print(
            f"[REPLAY SOURCE] requesting symbol={symbol}, "
            f"timeframe={timeframe}, date={replay_date}",
            flush=True,
        )

        if replay_date:
            start_ts = pd.to_datetime(replay_date, errors="coerce")

            if pd.isna(start_ts):
                raise ValueError(f"Invalid replay date: {replay_date}")

            start_dt = start_ts.normalize().to_pydatetime()

            today = self.market_calendar.exchange_now().date()
            if start_dt.date() > today:
                raise ValueError("Replay date cannot be in the future.")

            end_dt = start_dt + timedelta(days=1)

            print(
                f"[REPLAY SOURCE] loading range {start_dt} -> {end_dt}",
                flush=True,
            )

            df = self.rt.load_history_range(
                symbol,
                timeframe,
                start_dt,
                end_dt,
            )

            print(
                f"[RT HISTORY RANGE RESULT] {symbol} {timeframe} "
                f"{start_dt} -> {end_dt} rows={0 if df is None else len(df)}",
                flush=True,
            )

            if df is not None:
                print(
                    f"[RT HISTORY RANGE COLUMNS] {list(df.columns)}",
                    flush=True,
                )

            return df

        # If the live app already has bars for this symbol, use them.
        try:
            snap = self.rt.get_snapshot(symbol, timeframe)

            if snap.bars is not None and not snap.bars.empty:
                print(
                    f"[REPLAY SOURCE] using live snapshot bars "
                    f"{symbol} {timeframe} rows={len(snap.bars)}",
                    flush=True,
                )
                return snap.bars.copy()

        except Exception as snap_exc:
            print(f"[REPLAY SOURCE] live snapshot unavailable: {snap_exc}", flush=True)

        df = self.rt.load_history(symbol, timeframe)

        print(
            f"[RT HISTORY RESULT] {symbol} {timeframe} rows={0 if df is None else len(df)}",
            flush=True,
        )

        if df is not None:
            print(
                f"[RT HISTORY COLUMNS] {list(df.columns)}",
                flush=True,
            )

        return df

    # ------------------------------------------------------------------
    # Public history loader with cache validation
    # ------------------------------------------------------------------
    def get_history(
        self,
        symbol: str,
        timeframe: str = "1 min",
        replay_date: Optional[str] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        symbol = self.rt._sanitize_symbol(symbol)
        timeframe = timeframe or "1 min"
        key = self._make_cache_key(symbol, timeframe, replay_date)

        validate_full_session = self._should_validate_full_session(
            replay_date,
            timeframe,
        )

        if not force_refresh:
            cached = self.memory_cache.get(key)

            if cached is not None and not cached.empty:
                if validate_full_session:
                    ok, reason = self._cache_is_complete_for_replay_day(
                        cached,
                        replay_date,
                    )
                    print(
                        f"[REPLAY CACHE MEMORY CHECK] {key} ok={ok} reason={reason}",
                        flush=True,
                    )

                    if ok:
                        print(f"[REPLAY CACHE] memory hit {key}", flush=True)
                        return self._filter_regular_session(cached, replay_date)

                    print(
                        f"[REPLAY CACHE INVALIDATE MEMORY] {key} reason={reason}",
                        flush=True,
                    )
                    self.memory_cache.pop(key, None)
                else:
                    print(f"[REPLAY CACHE] memory hit {key}", flush=True)
                    return self._prepare_history_for_replay_date(cached, replay_date)

            disk_df = self.bar_store.read(symbol, timeframe, replay_date)

            if disk_df is not None and not disk_df.empty:
                if validate_full_session:
                    ok, reason = self._cache_is_complete_for_replay_day(
                        disk_df,
                        replay_date,
                    )
                    print(
                        f"[REPLAY CACHE DISK CHECK] {key} ok={ok} reason={reason}",
                        flush=True,
                    )

                    if ok:
                        prepared = self._filter_regular_session(disk_df, replay_date)
                        print(f"[REPLAY CACHE] disk hit {key}", flush=True)
                        self.memory_cache[key] = prepared.copy()
                        return prepared.copy()

                    print(
                        f"[REPLAY CACHE INVALIDATE DISK] {key} reason={reason}; "
                        "will refresh from IB/live source",
                        flush=True,
                    )
                else:
                    prepared = self._prepare_history_for_replay_date(
                        disk_df,
                        replay_date,
                    )
                    if not prepared.empty:
                        print(f"[REPLAY CACHE] disk hit {key}", flush=True)
                        self.memory_cache[key] = prepared.copy()
                        return prepared.copy()

        print(f"[REPLAY CACHE] IB/live load {key}", flush=True)

        hist = self._load_from_rt_or_ib(
            symbol=symbol,
            timeframe=timeframe,
            replay_date=replay_date,
        )

        try:
            print(
                f"[REPLAY SOURCE RESULT] symbol={symbol} timeframe={timeframe} "
                f"date={replay_date} rows={0 if hist is None else len(hist)} "
                f"columns={[] if hist is None else list(hist.columns)}",
                flush=True,
            )

            if hist is not None and not hist.empty and "time" in hist.columns:
                print(
                    f"[REPLAY SOURCE RESULT] first={hist['time'].iloc[0]} "
                    f"last={hist['time'].iloc[-1]}",
                    flush=True,
                )
        except Exception as debug_exc:
            print(f"[REPLAY SOURCE DEBUG ERROR] {debug_exc}", flush=True)

        prepared = self._prepare_history_for_replay_date(hist, replay_date)

        if prepared.empty:
            print(
                f"[REPLAY NO SESSION DATA] symbol={symbol} timeframe={timeframe} "
                f"date={replay_date}. Not saving empty cache.",
                flush=True,
            )
            return prepared.copy()

        should_save = True

        if validate_full_session:
            ok, reason = self._cache_is_complete_for_replay_day(
                prepared,
                replay_date,
            )
            print(
                f"[REPLAY REFRESH VALIDATION] {key} ok={ok} reason={reason}",
                flush=True,
            )

            if not ok:
                # Do not write incomplete historical days back into memory/disk
                # as if they were good replay source data.
                should_save = False
                print(
                    f"[REPLAY CACHE SKIP SAVE] {key} incomplete after refresh: {reason}",
                    flush=True,
                )

        if should_save:
            self.memory_cache[key] = prepared.copy()

            if not prepared.empty:
                self.bar_store.write(symbol, timeframe, replay_date, prepared)
                print(
                    f"[REPLAY CACHE SAVE] {key} rows={len(prepared):,} "
                    f"first={prepared['time'].iloc[0]} last={prepared['time'].iloc[-1]}",
                    flush=True,
                )

        return prepared.copy()

    # ------------------------------------------------------------------
    # Multi-day replay
    # ------------------------------------------------------------------
    def load_date_range(
        self,
        symbol: str,
        start_date,
        end_date,
        timeframe: str = "1 min",
        speed: Optional[float] = 1,
        force_refresh: bool = False,
        request_id: object = None,
    ) -> pd.DataFrame:
        """
        Load and stitch a replay range into one active replay dataset.

        Important:
            * Weekends are skipped.
            * Automatic mode chooses the finest source interval that stays
              within the replay bar budget.
            * Watch may resample that source into coarser display intervals.
            * Native daily bars are used only when the selected range is too
              large for a safe intraday source.
            * The stitched DataFrame is installed into ReplayEngine, so visible_bars(),
              current_bar(), info(), the replay slider, paper trading, and backtests all
              read from the same multi-day dataset.
        """

        self._require_current_load_request(request_id)
        symbol = self.rt._sanitize_symbol(symbol)

        start = pd.to_datetime(start_date, errors="coerce")
        end = pd.to_datetime(end_date, errors="coerce")

        if pd.isna(start) or pd.isna(end):
            raise ValueError("Invalid replay date range.")

        if end < start:
            start, end = end, start

        exchange_today = self.market_calendar.exchange_now().date()
        if start.date() > exchange_today or end.date() > exchange_today:
            raise ValueError("Replay date range cannot include future dates.")

        last_completed = self.market_calendar.last_completed_session()
        if end.date() > last_completed:
            end = pd.Timestamp(last_completed)

        if start.date() > last_completed:
            raise ValueError(
                "Replay requires a completed market session. "
                f"The latest available session is {last_completed.isoformat()}."
            )

        days = self.market_calendar.sessions_in_range(start, end)

        if not days:
            raise ValueError("No completed NYSE trading sessions found in selected range.")

        requested_timeframe = str(timeframe or "auto").lower().strip()

        if requested_timeframe in {"auto", "automatic"}:
            requested_timeframe = self.choose_range_source_timeframe(len(days))

        progress_timeframe = self.normalize_range_timeframe(requested_timeframe)
        self._require_current_load_request(request_id)
        self.begin_load_progress(
            len(days),
            progress_timeframe,
            request_id=request_id,
        )

        if requested_timeframe in {"1 day", "1d", "1 day bars"}:
            # Fetch the range once. Requesting each date separately with a
            # daily bar size would issue redundant one-year IB history calls.
            self._require_current_load_request(request_id)
            self.update_load_progress(
                completed_days=0,
                bars_loaded=0,
                message="Requesting daily range from IBKR...",
                request_id=request_id,
            )
            start_dt = start.normalize().to_pydatetime()
            end_dt = (end.normalize() + pd.Timedelta(days=1)).to_pydatetime()

            daily = self.rt.load_history_range(
                symbol,
                "1 day",
                start_dt,
                end_dt,
            )
            self._require_current_load_request(request_id)
            daily = self._normalize_replay_bars(daily)

            if not daily.empty:
                daily_dates = daily["time"].dt.date.astype(str)
                daily = daily[
                    daily_dates.isin(days)
                ].copy()
                daily = (
                    daily.sort_values("time")
                    .drop_duplicates(subset="time")
                    .reset_index(drop=True)
                )

            if daily.empty:
                self._require_current_load_request(request_id)
                self.fail_load_progress(
                    "No daily replay bars were returned.",
                    request_id=request_id,
                )
                raise ValueError("No daily replay bars found for selected date range.")

            self._install_replay_dataset(
                daily,
                symbol=symbol,
                timeframe="1 day",
                replay_date=start.date().isoformat(),
                replay_end_date=end.date().isoformat(),
                speed=speed,
                request_id=request_id,
            )

            print(
                f"[REPLAY RANGE DAILY] installed {len(daily):,} bars: "
                f"{symbol} {start.date().isoformat()} -> {end.date().isoformat()}.",
                flush=True,
            )
            self.finish_load_progress(
                len(daily),
                f"Loaded {len(daily):,} daily bars.",
                request_id=request_id,
            )
            return daily

        load_timeframe = self.normalize_range_timeframe(requested_timeframe)
        chunks: list[pd.DataFrame] = []
        bars_loaded = 0

        for day_number, day in enumerate(days, start=1):
            self._require_current_load_request(request_id)
            self.update_load_progress(
                completed_days=day_number - 1,
                bars_loaded=bars_loaded,
                message=f"Requesting {day} from IBKR...",
                request_id=request_id,
            )
            try:
                hist = self.get_history(
                    symbol=symbol,
                    timeframe=load_timeframe,
                    replay_date=day,
                    force_refresh=force_refresh,
                )
                self._require_current_load_request(request_id)
            except ReplayLoadCancelled:
                raise
            except Exception as exc:
                print(f"[REPLAY RANGE] {symbol} {day}: load failed: {exc}", flush=True)
                self.update_load_progress(
                    completed_days=day_number,
                    bars_loaded=bars_loaded,
                    message=f"Skipped {day}: request failed.",
                    request_id=request_id,
                )
                continue

            day_bars = self._filter_regular_session(hist, day)

            if day_bars is None or day_bars.empty:
                print(f"[REPLAY RANGE] {symbol} {day}: no regular-session bars.", flush=True)
                self.update_load_progress(
                    completed_days=day_number,
                    bars_loaded=bars_loaded,
                    message=f"Skipped {day}: no regular-session bars.",
                    request_id=request_id,
                )
                continue

            if load_timeframe == "1 min" and self._is_historical_replay_date(day):
                ok, reason = self._cache_is_complete_for_replay_day(day_bars, day)
                if not ok:
                    print(
                        f"[REPLAY RANGE] {symbol} {day}: incomplete session skipped: {reason}",
                        flush=True,
                    )
                    self.update_load_progress(
                        completed_days=day_number,
                        bars_loaded=bars_loaded,
                        message=f"Skipped {day}: incomplete session.",
                        request_id=request_id,
                    )
                    continue

            print(
                f"[REPLAY RANGE] {symbol} {day}: collected {len(day_bars):,} bars "
                f"first={day_bars['time'].iloc[0]} last={day_bars['time'].iloc[-1]}.",
                flush=True,
            )
            chunks.append(day_bars[OHLCV_COLUMNS].copy())
            bars_loaded += len(day_bars)
            self.update_load_progress(
                completed_days=day_number,
                bars_loaded=bars_loaded,
                message=f"Loaded {day} ({len(day_bars):,} bars).",
                request_id=request_id,
            )

        if not chunks:
            self._require_current_load_request(request_id)
            self.fail_load_progress(
                "No replay bars were found in the selected range.",
                request_id=request_id,
            )
            raise ValueError("No replay bars found for selected date range.")

        stitched = pd.concat(chunks, ignore_index=True)

        stitched = self._normalize_replay_bars(stitched)

        if stitched.empty:
            raise ValueError("Replay date range became empty after cleaning.")

        self._install_replay_dataset(
            stitched,
            symbol=symbol,
            timeframe=load_timeframe,
            replay_date=start.date().isoformat(),
            replay_end_date=end.date().isoformat(),
            speed=speed,
            request_id=request_id,
        )

        print(
            f"[REPLAY RANGE] installed stitched dataset: "
            f"{symbol} {start.date().isoformat()} -> {end.date().isoformat()} "
            f"{len(stitched):,} {load_timeframe} bars.",
            flush=True,
        )

        self.finish_load_progress(
            len(stitched),
            f"Loaded {len(stitched):,} {load_timeframe} bars.",
            request_id=request_id,
        )

        return stitched

    # ------------------------------------------------------------------
    # Single-day/latest replay
    # ------------------------------------------------------------------
    def load_replay(
        self,
        symbol: str,
        timeframe: str = "1 min",
        replay_date: Optional[str] = None,
        speed: Optional[float] = None,
        force_refresh: bool = False,
        force_reload: Optional[bool] = None,
        request_id: object = None,
    ) -> tuple[str, dict]:
        # force_reload is kept for backwards compatibility with older callbacks.
        if force_reload is not None:
            force_refresh = force_reload

        self._require_current_load_request(request_id)
        symbol = self.rt._sanitize_symbol(symbol)
        timeframe = timeframe or "1 min"
        requested_replay_date = replay_date
        adjustment = ""

        if replay_date:
            selected = pd.to_datetime(replay_date, errors="coerce")
            if pd.isna(selected):
                raise ValueError(f"Invalid replay date: {replay_date}")

            exchange_today = self.market_calendar.exchange_now().date()
            if selected.date() > exchange_today:
                raise ValueError("Replay date cannot be in the future.")

            last_completed = self.market_calendar.last_completed_session()
            if selected.date() == exchange_today and selected.date() != last_completed:
                replay_date = last_completed.isoformat()
            elif not self.market_calendar.is_session(selected.date()):
                replay_date = self.market_calendar.session_on_or_before(
                    selected.date()
                ).isoformat()

            if replay_date != requested_replay_date:
                adjustment = (
                    f" Requested {requested_replay_date}; using last completed "
                    f"session {replay_date}."
                )

        hist = self.get_history(
            symbol=symbol,
            timeframe=timeframe,
            replay_date=replay_date,
            force_refresh=force_refresh,
        )
        self._require_current_load_request(request_id)

        hist = self._prepare_history_for_replay_date(hist, replay_date)

        if hist is None or hist.empty:
            self._install_replay_dataset(
                hist,
                symbol=symbol,
                timeframe=timeframe,
                replay_date=replay_date,
                replay_end_date=replay_date,
                speed=speed,
                request_id=request_id,
            )

            date_label = replay_date or "latest"
            return (
                f"No replay history returned for {symbol} "
                f"({timeframe}, {date_label}).{adjustment}"
            ), {
                "playing": False,
                "speed": self.engine.speed,
                "current_index": 1,
                "max_index": 0,
            }

        warning = ""
        if self._should_validate_full_session(replay_date, timeframe):
            ok, reason = self._cache_is_complete_for_replay_day(hist, replay_date)
            if not ok:
                warning = f" · WARNING incomplete session: {reason}"

        self._install_replay_dataset(
            hist,
            symbol=symbol,
            timeframe=timeframe,
            replay_date=replay_date,
            replay_end_date=replay_date,
            speed=speed,
            request_id=request_id,
        )

        date_label = replay_date or "latest"
        return (
            f"Replay loaded for {symbol} ({timeframe}, {date_label}, "
            f"{len(hist)} bars){warning}.{adjustment}",
            self.engine.info(),
        )

    # ------------------------------------------------------------------
    # Replay controls
    # ------------------------------------------------------------------
    def play(self) -> None:
        self.engine.play()

    def pause(self) -> None:
        self.engine.pause()

    def rewind(self, steps: int = 1) -> None:
        self.engine.rewind(steps)

    def forward(self, steps: int = 1) -> None:
        self.engine.forward(steps)

    def set_index(self, index: int) -> None:
        self.engine.set_index(index)

    def set_speed(self, speed: float) -> None:
        self.engine.set_speed(speed)

    def tick(self) -> None:
        self.engine.tick()

    # ------------------------------------------------------------------
    # Replay data accessors
    # ------------------------------------------------------------------
    def all_bars(self) -> pd.DataFrame:
        """
        Return the full loaded replay dataset.

        This is what backtests should use.
        It includes the full stitched date range when a replay range is loaded.
        """
        if self.engine is None or self.engine.bars is None:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        return self.engine.bars.copy()

    def full_bars(self) -> pd.DataFrame:
        return self.all_bars()

    def loaded_bars(self) -> pd.DataFrame:
        return self.all_bars()

    def visible_bars(self) -> pd.DataFrame:
        return self.engine.visible_bars()

    def current_bar(self):
        return self.engine.current_bar()

    def info(self) -> dict:
        return self.engine.info()

    def reset(self) -> None:
        self.engine.reset()
