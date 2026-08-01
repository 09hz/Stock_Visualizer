from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from core.RealTime import RealTimeIB
from core.ReplayModule import ReplayEngine
from services.bar_store import BarStore


OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


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

    # A normal US regular session is about 390 one-minute bars.
    # Keep this slightly below 390 so an occasional missing bar does not force
    # endless refreshes. Adjust later if you add half-day calendar support.
    MIN_HISTORICAL_1MIN_ROWS = 360

    def __init__(
        self,
        rt: RealTimeIB,
        engine: ReplayEngine,
        bar_store: Optional[BarStore] = None,
    ):
        self.rt = rt
        self.engine = engine
        self.bar_store = bar_store or BarStore()
        self.memory_cache: dict[tuple[str, str, str], pd.DataFrame] = {}

        self.current_symbol: Optional[str] = None
        self.current_timeframe: str = "1 min"
        self.current_replay_date: Optional[str] = None
        self.current_replay_end_date: Optional[str] = None

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
        selected = pd.to_datetime(replay_date, errors="coerce")

        if pd.isna(selected):
            selected = pd.Timestamp.now()

        selected = selected.normalize()

        session_start = selected.replace(hour=9, minute=30, second=0, microsecond=0)
        session_end = selected.replace(hour=16, minute=0, second=0, microsecond=0)

        return session_start, session_end

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

        return selected.date() < pd.Timestamp.now().date()

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
        min_rows = int(min_rows or self.MIN_HISTORICAL_1MIN_ROWS)

        df = self._filter_regular_session(bars, replay_date)

        if df.empty:
            return False, "empty regular-session cache"

        session_start, session_end = self._session_bounds_for_date(replay_date)

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

            today = datetime.now().date()
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
    ) -> pd.DataFrame:
        """
        Load and stitch a replay range into one active replay dataset.

        Important:
            * Weekends are skipped.
            * Intraday Watch intervals use raw 1-minute replay bars.
            * The 1-day Watch interval loads native daily bars so each trading
              date is represented by exactly one source bar.
            * The stitched DataFrame is installed into ReplayEngine, so visible_bars(),
              current_bar(), info(), the replay slider, paper trading, and backtests all
              read from the same multi-day dataset.
        """

        symbol = self.rt._sanitize_symbol(symbol)

        start = pd.to_datetime(start_date, errors="coerce")
        end = pd.to_datetime(end_date, errors="coerce")

        if pd.isna(start) or pd.isna(end):
            raise ValueError("Invalid replay date range.")

        if end < start:
            start, end = end, start

        today = datetime.now().date()
        if start.date() > today or end.date() > today:
            raise ValueError("Replay date range cannot include future dates.")

        days: list[str] = []
        current = start.normalize()

        while current <= end.normalize():
            if int(current.weekday()) < 5:
                days.append(current.date().isoformat())
            current = current + pd.Timedelta(days=1)

        if not days:
            raise ValueError("No weekday trading days found in selected range.")

        requested_timeframe = str(timeframe or "1 min").lower().strip()

        if requested_timeframe in {"1 day", "1d", "1 day bars"}:
            # Fetch the range once. Requesting each date separately with a
            # daily bar size would issue redundant one-year IB history calls.
            start_dt = start.normalize().to_pydatetime()
            end_dt = (end.normalize() + pd.Timedelta(days=1)).to_pydatetime()

            daily = self.rt.load_history_range(
                symbol,
                "1 day",
                start_dt,
                end_dt,
            )
            daily = self._normalize_replay_bars(daily)

            if not daily.empty:
                daily_dates = daily["time"].dt.normalize()
                daily = daily[
                    (daily_dates >= start.normalize())
                    & (daily_dates <= end.normalize())
                    & (daily["time"].dt.weekday < 5)
                ].copy()
                daily = (
                    daily.sort_values("time")
                    .drop_duplicates(subset="time")
                    .reset_index(drop=True)
                )

            if daily.empty:
                raise ValueError("No daily replay bars found for selected date range.")

            self.current_symbol = symbol
            self.current_timeframe = "1 day"
            self.current_replay_date = start.date().isoformat()
            self.current_replay_end_date = end.date().isoformat()

            self.engine.reset()
            self.engine.load_from_df(daily)

            if speed is not None:
                self.engine.set_speed(speed)

            print(
                f"[REPLAY RANGE DAILY] installed {len(daily):,} bars: "
                f"{symbol} {start.date().isoformat()} -> {end.date().isoformat()}.",
                flush=True,
            )
            return daily

        # The replay engine uses 1-minute source bars. Display intervals are handled
        # later by Watch chart rendering / BarViewService resampling.
        load_timeframe = "1 min"
        chunks: list[pd.DataFrame] = []

        for day in days:
            try:
                hist = self.get_history(
                    symbol=symbol,
                    timeframe=load_timeframe,
                    replay_date=day,
                    force_refresh=force_refresh,
                )
            except Exception as exc:
                print(f"[REPLAY RANGE] {symbol} {day}: load failed: {exc}", flush=True)
                continue

            day_bars = self._filter_regular_session(hist, day)

            if day_bars is None or day_bars.empty:
                print(f"[REPLAY RANGE] {symbol} {day}: no regular-session bars.", flush=True)
                continue

            if self._is_historical_replay_date(day):
                ok, reason = self._cache_is_complete_for_replay_day(day_bars, day)
                if not ok:
                    print(
                        f"[REPLAY RANGE] {symbol} {day}: incomplete session skipped: {reason}",
                        flush=True,
                    )
                    continue

            print(
                f"[REPLAY RANGE] {symbol} {day}: collected {len(day_bars):,} bars "
                f"first={day_bars['time'].iloc[0]} last={day_bars['time'].iloc[-1]}.",
                flush=True,
            )
            chunks.append(day_bars[OHLCV_COLUMNS].copy())

        if not chunks:
            raise ValueError("No replay bars found for selected date range.")

        stitched = pd.concat(chunks, ignore_index=True)

        stitched = self._normalize_replay_bars(stitched)

        if stitched.empty:
            raise ValueError("Replay date range became empty after cleaning.")

        self.current_symbol = symbol
        self.current_timeframe = load_timeframe
        self.current_replay_date = start.date().isoformat()
        self.current_replay_end_date = end.date().isoformat()

        self.engine.reset()
        self.engine.load_from_df(stitched)

        if speed is not None:
            self.engine.set_speed(speed)

        print(
            f"[REPLAY RANGE] installed stitched dataset: "
            f"{symbol} {start.date().isoformat()} -> {end.date().isoformat()} "
            f"{len(stitched):,} bars.",
            flush=True,
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
    ) -> tuple[str, dict]:
        # force_reload is kept for backwards compatibility with older callbacks.
        if force_reload is not None:
            force_refresh = force_reload

        symbol = self.rt._sanitize_symbol(symbol)
        timeframe = timeframe or "1 min"

        hist = self.get_history(
            symbol=symbol,
            timeframe=timeframe,
            replay_date=replay_date,
            force_refresh=force_refresh,
        )

        hist = self._prepare_history_for_replay_date(hist, replay_date)

        self.current_symbol = symbol
        self.current_timeframe = timeframe
        self.current_replay_date = replay_date
        self.current_replay_end_date = replay_date

        if hist is None or hist.empty:
            self.engine.reset()
            if speed is not None:
                self.engine.set_speed(speed)

            date_label = replay_date or "latest"
            return f"No replay history returned for {symbol} ({timeframe}, {date_label})", {
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

        self.engine.reset()
        self.engine.load_from_df(hist)

        if speed is not None:
            self.engine.set_speed(speed)

        date_label = replay_date or "latest"
        return (
            f"Replay loaded for {symbol} ({timeframe}, {date_label}, {len(hist)} bars){warning}",
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
