from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go


class ChartViewportService:
    """
    Owns chart viewport behavior: range buttons, manual zoom/pan state,
    visible-window y-axis fitting, and future follow/manual/range modes.

    This service intentionally does not load data, mutate replay state,
    run strategies, or touch paper trading state.
    """

    RANGE_DAYS = {
        "1D": 1,
        "1W": 7,
        "1M": 30,
        "3M": 90,
        "1Y": 365,
        "5Y": 365 * 5,
    }

    VALID_RANGE_KEYS = {"1D", "1W", "1M", "3M", "1Y", "5Y", "MAX"}

    def safe_range_key(self, value, default: str = "1D") -> str:
        value = str(value or default).upper().strip()
        if value in self.VALID_RANGE_KEYS:
            return value
        return default

    def default_state(self, range_key: str = "1D") -> dict:
        return {
            "mode": "live",
            "range_key": self.safe_range_key(range_key),
            "x_range": None,
            "y_range": None,
        }

    def clean_relayout_range(self, relayout_data):
        """
        Convert Plotly relayoutData into viewport state.

        Returns:
            dict when viewport state changed
            None when relayoutData should be ignored
        """

        if not relayout_data:
            return None

        if (
            relayout_data.get("xaxis.autorange") is True
            or relayout_data.get("yaxis.autorange") is True
            or relayout_data.get("autosize") is True
        ):
            return {
                "mode": "live",
                "x_range": None,
                "y_range": None,
            }

        x0 = relayout_data.get("xaxis.range[0]")
        x1 = relayout_data.get("xaxis.range[1]")
        y0 = relayout_data.get("yaxis.range[0]")
        y1 = relayout_data.get("yaxis.range[1]")

        if x0 is not None and x1 is not None:
            return {
                "mode": "manual",
                "x_range": [x0, x1],
                "y_range": [y0, y1] if y0 is not None and y1 is not None else None,
            }

        if y0 is not None and y1 is not None:
            return {
                "mode": "manual",
                "x_range": None,
                "y_range": [y0, y1],
            }

        return None

    def clean_bars_for_view(self, bars: pd.DataFrame | None) -> pd.DataFrame:
        if bars is None or bars.empty:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        df = bars.copy()

        if "time" not in df.columns:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        df["time"] = pd.to_datetime(df["time"], errors="coerce", format="mixed")

        for col in ["high", "low"]:
            if col not in df.columns:
                return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["time", "high", "low"]).copy()
        return df

    def visible_window_from_bars(self, bars: pd.DataFrame, range_key: str):
        df = self.clean_bars_for_view(bars)

        if df.empty:
            return None

        range_key = self.safe_range_key(range_key)
        end_time = df["time"].max()

        if range_key == "MAX":
            start_time = df["time"].min()
        else:
            days = self.RANGE_DAYS.get(range_key, 1)
            start_time = end_time - timedelta(days=days)
            start_time = max(start_time, df["time"].min())

        return [start_time, end_time]

    def pad_x_range(self, bars: pd.DataFrame, x_range):
        """Add half a candle of space at each horizontal edge."""

        if not x_range:
            return x_range

        df = self.clean_bars_for_view(bars)
        if df.empty:
            return x_range

        times = df["time"].drop_duplicates().sort_values()
        intervals = times.diff().dropna()
        intervals = intervals[intervals > pd.Timedelta(0)]

        if intervals.empty:
            # A single daily candle still needs enough room to be visible.
            padding = pd.Timedelta(hours=12)
        else:
            padding = intervals.median() / 2

        return [x_range[0] - padding, x_range[1] + padding]

    def fit_y_axis_to_visible_bars(
        self,
        fig: go.Figure,
        bars: pd.DataFrame,
        x_range=None,
    ) -> go.Figure:
        """
        Fit y-axis only to visible candles. This prevents candles from becoming
        flat or unreadable after zoom/range changes.
        """

        df = self.clean_bars_for_view(bars)

        if df.empty:
            return fig

        visible = df

        if x_range:
            x0 = pd.to_datetime(x_range[0], errors="coerce")
            x1 = pd.to_datetime(x_range[1], errors="coerce")

            if pd.notna(x0) and pd.notna(x1):
                visible = df[(df["time"] >= x0) & (df["time"] <= x1)]

        if visible.empty:
            visible = df.tail(100)

        high = float(visible["high"].max())
        low = float(visible["low"].min())

        if high <= low:
            pad = max(abs(high) * 0.005, 0.01)
        else:
            pad = (high - low) * 0.08

        fig.update_yaxes(range=[low - pad, high + pad], fixedrange=False)
        return fig

    def apply_chart_view(
        self,
        fig: go.Figure,
        bars: pd.DataFrame,
        chart_state: dict | None,
        default_range: str = "1D",
        range_bars: pd.DataFrame | None = None,
    ) -> go.Figure:
        """
        Apply the saved viewport without changing which candles are rendered.

        ``bars`` contains the cursor-visible candles and remains the source for
        y-axis fitting. ``range_bars`` may contain the complete loaded range so
        replay can reserve the full horizontal space before every candle has
        been revealed.
        """
        state = chart_state or {}
        mode = state.get("mode", "live")
        range_key = self.safe_range_key(state.get("range_key"), default_range)

        if bars is None or bars.empty:
            return fig

        if mode == "manual":
            x_range = state.get("x_range")
            y_range = state.get("y_range")

            if x_range:
                fig.update_xaxes(range=x_range, fixedrange=False)
                fig = self.fit_y_axis_to_visible_bars(fig, bars, x_range)

            if y_range:
                fig.update_yaxes(range=y_range, fixedrange=False)

            return fig

        range_source = range_bars
        if range_source is None or range_source.empty:
            range_source = bars

        # For an intraday replay range containing several sessions, frame the
        # complete session at the cursor instead of jumping ahead to the final
        # loaded date. Daily replay uses MAX and therefore keeps every loaded
        # date in view.
        if range_key == "1D" and range_bars is not None and not range_bars.empty:
            visible_df = self.clean_bars_for_view(bars)
            complete_df = self.clean_bars_for_view(range_bars)
            if not visible_df.empty and not complete_df.empty:
                cursor_day = visible_df["time"].max().normalize()
                current_session = complete_df[
                    complete_df["time"].dt.normalize() == cursor_day
                ]
                if not current_session.empty:
                    range_source = current_session

        x_range = self.visible_window_from_bars(range_source, range_key)
        x_range = self.pad_x_range(range_source, x_range)

        if x_range:
            fig.update_xaxes(range=x_range, fixedrange=False)

        fig = self.fit_y_axis_to_visible_bars(fig, bars, x_range)
        return fig
