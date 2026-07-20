from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
import plotly.graph_objects as go


OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def _to_tz_naive_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)

    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)

    return ts


def normalize_history_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    out = df.copy()

    rename_map = {}
    for src, dst in [
        ("date", "time"),
        ("Date", "time"),
        ("datetime", "time"),
        ("Datetime", "time"),
    ]:
        if src in out.columns and dst not in out.columns:
            rename_map[src] = dst

    if rename_map:
        out = out.rename(columns=rename_map)

    for col in OHLCV_COLUMNS:
        if col not in out.columns:
            if col == "volume":
                out[col] = 0
            else:
                raise ValueError(f"Missing required column: {col}")

    out = out[OHLCV_COLUMNS].copy()

    out["time"] = out["time"].apply(_to_tz_naive_timestamp)
    out["open"] = pd.to_numeric(out["open"], errors="coerce")
    out["high"] = pd.to_numeric(out["high"], errors="coerce")
    out["low"] = pd.to_numeric(out["low"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0)

    out = out.dropna(subset=["time", "open", "high", "low", "close"])
    out = out.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)

    return out


def _floor_time_to_minute(ts: datetime) -> pd.Timestamp:
    return _to_tz_naive_timestamp(ts).floor("min")


def apply_tick_to_bars(
    bars: pd.DataFrame,
    price: float,
    size: float,
    tick_time: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Patch one live tick into the latest OHLCV bar.

    If the tick timestamp is behind the latest historical candle because of an
    IB/local timezone mismatch, the newest visible candle is still patched. This
    keeps candle shapes moving whenever the live price updates.
    """
    if tick_time is None:
        tick_time = datetime.now()

    out = normalize_history_df(bars)
    bar_time = _floor_time_to_minute(tick_time)
    price = float(price)
    size = float(size or 0)

    if out.empty:
        return pd.DataFrame(
            [
                {
                    "time": bar_time,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": size,
                }
            ]
        )

    last_idx = out.index[-1]
    last_bar_time = _floor_time_to_minute(out.loc[last_idx, "time"])

    if bar_time > last_bar_time:
        new_row = {
            "time": bar_time,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": size,
        }
        return pd.concat([out, pd.DataFrame([new_row])], ignore_index=True)

    # Same minute or timestamp mismatch: update latest visible candle.
    out.loc[last_idx, "high"] = max(float(out.loc[last_idx, "high"]), price)
    out.loc[last_idx, "low"] = min(float(out.loc[last_idx, "low"]), price)
    out.loc[last_idx, "close"] = price
    out.loc[last_idx, "volume"] = float(out.loc[last_idx, "volume"]) + size

    return out


def resample_bars(bars: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    out = normalize_history_df(bars)
    if out.empty or timeframe == "1 min":
        return out

    rule_map = {
        "5 mins": "5min",
        "15 mins": "15min",
        "1 hour": "1h",
        "1 day": "1D",
    }

    if timeframe not in rule_map:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    out = out.set_index("time")
    resampled = out.resample(rule_map[timeframe]).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    resampled = resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return resampled


def create_candlestick_figure(
    bars: pd.DataFrame,
    symbol: str,
    timeframe: str,
    current_price: Optional[float] = None,
) -> go.Figure:
    df = normalize_history_df(bars)

    # Prevent startup or very large datasets from causing unreadable initial
    # candle widths. The callbacks still control the actual visible range.
    if not df.empty:
        df = df.tail(1500).copy()

    fig = go.Figure()

    if not df.empty:
        fig.add_trace(
            go.Candlestick(
                x=df["time"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name=f"{symbol} {timeframe}",
                increasing_line_color="#22c55e",
                increasing_fillcolor="#22c55e",
                decreasing_line_color="#ef4444",
                decreasing_fillcolor="#ef4444",
                whiskerwidth=0.4,
            )
        )

        price_for_line = current_price
        if price_for_line is None:
            price_for_line = float(df.iloc[-1]["close"])

        fig.add_hline(
            y=float(price_for_line),
            line_width=1.2,
            line_dash="dot",
            line_color="white",
            opacity=0.95,
            annotation_text=f"{float(price_for_line):,.2f}",
            annotation_position="right",
            annotation_font=dict(color="black", size=12),
            annotation_bgcolor="white",
            annotation_bordercolor="black",
        )

    fig.update_layout(
        paper_bgcolor="black",
        plot_bgcolor="black",
        font={"color": "white"},
        dragmode="pan",
        hovermode="x unified",
        margin={"l": 40, "r": 20, "t": 50, "b": 40},
    )

    fig.update_xaxes(
        rangebreaks=[
            dict(bounds=["sat", "mon"]),
            dict(bounds=[16, 9.5], pattern="hour"),
        ],
        rangeslider_visible=False,
    )

    fig.update_yaxes(fixedrange=False)


    fig.update_yaxes(
        showgrid=True,
        gridcolor="white",
        zeroline=False,
        showline=False,
        side="right",
        fixedrange=False,
    )

    return fig
