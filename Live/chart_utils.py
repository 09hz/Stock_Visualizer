from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def normalize_history_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    out = df.copy()
    if "date" in out.columns:
        out = out.rename(columns={"date": "time"})

    expected = ["time", "open", "high", "low", "close", "volume"]
    missing = [c for c in expected if c not in out.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    out = out[expected].copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce").dt.tz_localize(None)
    out = out.dropna(subset=["time"]).sort_values("time").drop_duplicates(subset="time")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype(int)

    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    return out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def floor_to_minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def apply_tick_to_bars(
    df: pd.DataFrame,
    price: float,
    size: float,
    tick_time: Optional[datetime] = None
) -> pd.DataFrame:
    if tick_time is None:
        tick_time = datetime.now()

    tick_minute = floor_to_minute(tick_time)

    if df is None or df.empty:
        return pd.DataFrame([{
            "time": tick_minute,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": int(size or 0),
        }])

    out = df.copy()
    last_time = pd.to_datetime(out.iloc[-1]["time"]).to_pydatetime().replace(tzinfo=None)
    last_minute = floor_to_minute(last_time)
    size_int = int(size or 0)

    if tick_minute == last_minute:
        idx = out.index[-1]
        out.at[idx, "close"] = price
        out.at[idx, "high"] = max(float(out.at[idx, "high"]), price)
        out.at[idx, "low"] = min(float(out.at[idx, "low"]), price)
        out.at[idx, "volume"] = int(out.at[idx, "volume"]) + size_int
    else:
        new_row = pd.DataFrame([{
            "time": tick_minute,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": size_int,
        }])
        out = pd.concat([out, new_row], ignore_index=True)

    return out


def resample_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    if timeframe == "1 min":
        return df.copy()

    rule_map = {
        "5 mins": "5min",
        "15 mins": "15min",
        "1 hour": "1h",
        "1 day": "1D",
    }

    if timeframe not in rule_map:
        raise ValueError(f"Unsupported timeframe for resample: {timeframe}")

    out = df.copy()
    out["time"] = pd.to_datetime(out["time"])
    out = out.set_index("time")

    resampled = out.resample(rule_map[timeframe]).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })

    return resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()


def create_candlestick_figure(df: pd.DataFrame, symbol: str, timeframe: str) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.72, 0.28],
    )

    if df is None or df.empty:
        fig.update_layout(
            title=f"{symbol} | {timeframe} | No data",
            template="plotly_dark",
            xaxis_rangeslider_visible=False,
            height=820,
        )
        return fig

    colors = ["green" if c >= o else "red" for c, o in zip(df["close"], df["open"])]

    fig.add_trace(
        go.Candlestick(
            x=df["time"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=symbol,
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=df["time"],
            y=df["volume"],
            marker_color=colors,
            name="Volume",
            showlegend=False,
            opacity=0.55,
        ),
        row=2,
        col=1,
    )

    current_price = float(df.iloc[-1]["close"])
    session_open = float(df.iloc[0]["open"])
    change = current_price - session_open
    change_pct = (change / session_open * 100) if session_open else 0.0
    title_color = "#00cc96" if change >= 0 else "#ef553b"

    fig.update_layout(
        title={
            "text": f"{symbol} | {timeframe} | Last: ${current_price:.2f} | Change: {change:+.2f} ({change_pct:+.2f}%)",
            "x": 0.5,
            "xanchor": "center",
            "font": {"color": title_color},
        },
        template="plotly_dark",
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        font={"family": "Arial, Helvetica, sans-serif", "size": 13, "color": "#e5e7eb"},
        xaxis_rangeslider_visible=False,
        height=820,
        margin=dict(l=40, r=30, t=70, b=40),
        hovermode="x unified",
    )

    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="#1f2630", row=1, col=1)
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="#1f2630", row=1, col=1)
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="#1f2630", row=2, col=1)
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="#1f2630", row=2, col=1)

    return fig