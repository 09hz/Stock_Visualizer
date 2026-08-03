from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from models.watch_models import WatchBarsView


OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


class BarViewService:
    """
    Builds clean Watch chart bar snapshots.

    This is the first step in removing heavy data-prep logic from callbacks.py.
    It does not mutate ReplayService or RealTime state except for optionally
    requesting a live symbol.
    """

    TIMEFRAME_ALIASES = {
        "1min": "1 min",
        "1m": "1 min",
        "1 min": "1 min",
        "5min": "5 mins",
        "5m": "5 mins",
        "5 min": "5 mins",
        "5 mins": "5 mins",
        "15min": "15 mins",
        "15m": "15 mins",
        "15 min": "15 mins",
        "15 mins": "15 mins",
        "30min": "30 mins",
        "30m": "30 mins",
        "30 min": "30 mins",
        "30 mins": "30 mins",
        "1h": "1 hour",
        "1 hour": "1 hour",
        "1d": "1 day",
        "1 day": "1 day",
    }

    RESAMPLE_RULES = {
        "1 min": None,
        "5 mins": "5min",
        "15 mins": "15min",
        "30 mins": "30min",
        "1 hour": "1h",
        "1 day": "1D",
    }

    def normalize_timeframe(self, timeframe: str | None) -> str:
        raw = str(timeframe or "1 min").strip().lower()
        return self.TIMEFRAME_ALIASES.get(raw, str(timeframe or "1 min").strip())

    def empty_view(
        self,
        *,
        symbol: str,
        display_timeframe: str,
        source: str = "empty",
        error: str | None = None,
        current_index: int = 1,
        max_index: int = 1,
    ) -> WatchBarsView:
        empty = pd.DataFrame(columns=OHLCV_COLUMNS)
        safe_source = source if source in {"live", "replay", "empty", "error"} else "empty"
        return WatchBarsView(
            source=safe_source,
            symbol=str(symbol or "").upper().strip(),
            display_timeframe=self.normalize_timeframe(display_timeframe),
            full_bars=empty.copy(),
            visible_bars=empty.copy(),
            chart_bars=empty.copy(),
            current_price=None,
            updated_at=datetime.now(),
            current_index=max(1, int(current_index or 1)),
            max_index=max(1, int(max_index or 1)),
            chart_label="Empty",
            error=error,
        )

    def clean_bars(self, bars: pd.DataFrame | None) -> pd.DataFrame:
        if bars is None or bars.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        df = bars.copy()

        if "time" not in df.columns:
            df["time"] = pd.NaT

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

        df = df.sort_values("time").reset_index(drop=True)
        return df[OHLCV_COLUMNS].copy()

    def resample_bars(
        self,
        bars: pd.DataFrame | None,
        display_timeframe: str | None,
    ) -> pd.DataFrame:
        df = self.clean_bars(bars)
        timeframe = self.normalize_timeframe(display_timeframe)

        if df.empty:
            return df

        rule = self.RESAMPLE_RULES.get(timeframe)

        if rule is None:
            return df

        work = df.copy().set_index("time")

        resample_kwargs = {}
        if timeframe == "1 hour":
            # US regular trading starts at 09:30. Anchor hourly candles to the
            # session open instead of pandas' default 09:00 clock boundary.
            resample_kwargs = {"origin": "start_day", "offset": "30min"}

        out = work.resample(rule, **resample_kwargs).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )

        out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
        return self.clean_bars(out)

    def get_replay_full_bars(self, replay_service: Any) -> pd.DataFrame:
        """
        Best-effort access to full replay data. Supports accessor names we have
        added during previous refactors.
        """
        for attr in ("all_bars", "full_bars", "loaded_bars"):
            try:
                method = getattr(replay_service, attr, None)
                if callable(method):
                    bars = method()
                    if bars is not None and not bars.empty:
                        return self.clean_bars(bars)
            except Exception:
                pass

        try:
            engine = getattr(replay_service, "engine", None)
            bars = getattr(engine, "bars", None)
            if bars is not None and not bars.empty:
                return self.clean_bars(bars)
        except Exception:
            pass

        return pd.DataFrame(columns=OHLCV_COLUMNS)

    def build_watch_view(
        self,
        *,
        rt: Any,
        replay_service: Any,
        symbol: str,
        display_timeframe: str,
        use_live_watch_data: bool,
    ) -> WatchBarsView:
        symbol = str(symbol or "").upper().strip()
        display_timeframe = self.normalize_timeframe(display_timeframe)

        try:
            info = replay_service.info()
        except Exception:
            info = {}

        max_index = max(1, int(info.get("max_index", 1) or 1))
        current_index = max(1, int(info.get("current_index", 1) or 1))

        if use_live_watch_data:
            try:
                try:
                    rt.request_symbol(symbol)
                except Exception:
                    pass

                snap = rt.get_snapshot(symbol, "1 min")
                live_bars = self.clean_bars(getattr(snap, "bars", None))
                chart_bars = self.resample_bars(live_bars, display_timeframe)

                current_price = getattr(snap, "last", None)
                if current_price is None and not live_bars.empty:
                    current_price = float(live_bars.iloc[-1]["close"])

                return WatchBarsView(
                    source="live",
                    symbol=symbol,
                    display_timeframe=display_timeframe,
                    full_bars=live_bars.copy(),
                    visible_bars=live_bars.copy(),
                    chart_bars=chart_bars,
                    current_price=float(current_price) if current_price is not None else None,
                    updated_at=getattr(snap, "updated_at", None) or datetime.now(),
                    current_index=current_index,
                    max_index=max_index,
                    chart_label="Live Market",
                    error=None,
                )
            except Exception as exc:
                return self.empty_view(
                    symbol=symbol,
                    display_timeframe=display_timeframe,
                    source="error",
                    error=f"Live data error: {exc}",
                    current_index=current_index,
                    max_index=max_index,
                )

        try:
            visible = self.clean_bars(replay_service.visible_bars())
        except Exception as exc:
            return self.empty_view(
                symbol=symbol,
                display_timeframe=display_timeframe,
                source="error",
                error=f"Replay visible_bars error: {exc}",
                current_index=current_index,
                max_index=max_index,
            )

        full_bars = self.get_replay_full_bars(replay_service)
        if full_bars.empty:
            full_bars = visible.copy()

        # All replay intervals remain cursor-based. For native daily ranges,
        # each replay step reveals exactly one additional daily candle.
        chart_bars = self.resample_bars(visible, display_timeframe)

        current_price = None
        if not visible.empty:
            current_price = float(visible.iloc[-1]["close"])

        return WatchBarsView(
            source="replay",
            symbol=symbol,
            display_timeframe=display_timeframe,
            full_bars=full_bars,
            visible_bars=visible,
            chart_bars=chart_bars,
            current_price=current_price,
            updated_at=datetime.now(),
            current_index=current_index,
            max_index=max_index,
            chart_label="Replay Cursor",
            error=None,
        )
