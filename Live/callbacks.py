# =============================================================================
# CALLBACK ORDER WARNING
# =============================================================================
# This app is sensitive to callback order because several callbacks share
# interval/store-trigger dependencies.
#
# Keep callback order stable. Render callbacks should not mutate replay/paper/live
# service state except for safe snapshot reads. State mutation should happen in
# control callbacks, then trigger render callbacks through dcc.Store values.
# =============================================================================
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import time



import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, html, dcc, no_update, ctx

from config import DEFAULT_SYMBOL, DEFAULT_TIMEFRAME
from utils.chart_utils import create_candlestick_figure
from core.RiskGuard import TradeIntent
from core.StrategyEngine import StrategyEngine
from core.BackTestEngine import BackTestEngine

from services.strategy_overlay_service import StrategyOverlayService
from services.bar_view_service import BarViewService
from services.chart_viewport_service import ChartViewportService

from renderers.watch_chart_renderer import WatchChartRenderer
from renderers.strategy_overlay_renderer import StrategyOverlayRenderer


try:
    from core.StrategyFunctionRegistry import get_function_reference_markdown
except Exception:
    def get_function_reference_markdown() -> str:
        return (
            "# Strategy Function Reference\n\n"
            "Function registry could not be imported. "
            "Make sure `Live/core/StrategyFunctionRegistry.py` exists."
        )

RANGE_DAYS = {
    "1D": 1,
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "5Y": 365 * 5,
}


def _build_metrics_strip(symbol: str, company: str, last, open_, updated_at, prefix: str = "USD"):
    if last is None:
        return [
            html.Div(f"{symbol} / {company}", className="metric-price"),
            html.Div("Waiting for data...", className="metric-muted"),
        ]

    last_f = float(last)
    open_f = float(open_) if open_ not in (None, 0) else last_f
    change = last_f - open_f
    pct = (change / open_f * 100) if open_f else 0.0
    cls = "metric-positive" if change >= 0 else "metric-negative"
    updated_text = updated_at.strftime("%A, %I:%M %p") if updated_at else "--"

    return [
        html.Div(f"{last_f:,.2f} {prefix}", className="metric-price"),
        html.Div(f"{change:+.2f} ({pct:+.2f}%)", className=cls),
        html.Div(f"{symbol} · {company}", className="metric-muted"),
        html.Div(f"Updated {updated_text}", className="metric-muted"),
    ]


def _build_stats_grid_from_bars(df):
    if df is None or df.empty:
        return [
            html.Div(
                className="stat-card",
                children=[html.Div("No data loaded", className="stat-label")],
            )
        ]

    first = df.iloc[0]
    last = df.iloc[-1]

    open_v = float(first["open"])
    high_v = float(df["high"].max())
    low_v = float(df["low"].min())
    close_v = float(last["close"])
    volume_v = float(df["volume"].sum())

    return [
        html.Div(
            className="stat-card",
            children=[
                html.Div(className="stat-row", children=[html.Div("Open", className="stat-label"), html.Div(f"{open_v:,.2f}", className="stat-value")]),
                html.Div(className="stat-row", children=[html.Div("High", className="stat-label"), html.Div(f"{high_v:,.2f}", className="stat-value")]),
                html.Div(className="stat-row", children=[html.Div("Low", className="stat-label"), html.Div(f"{low_v:,.2f}", className="stat-value")]),
            ],
        ),
        html.Div(
            className="stat-card",
            children=[
                html.Div(className="stat-row", children=[html.Div("Close", className="stat-label"), html.Div(f"{close_v:,.2f}", className="stat-value")]),
                html.Div(className="stat-row", children=[html.Div("Bars", className="stat-label"), html.Div(f"{len(df):,}", className="stat-value")]),
                html.Div(className="stat-row", children=[html.Div("Volume", className="stat-label"), html.Div(f"{volume_v:,.0f}", className="stat-value")]),
            ],
        ),
        html.Div(
            className="stat-card",
            children=[
                html.Div(className="stat-row", children=[html.Div("Range", className="stat-label"), html.Div(f"{high_v - low_v:,.2f}", className="stat-value")]),
                html.Div(className="stat-row", children=[html.Div("First Bar", className="stat-label"), html.Div(str(first["time"])[:16], className="stat-value")]),
                html.Div(className="stat-row", children=[html.Div("Last Bar", className="stat-label"), html.Div(str(last["time"])[:16], className="stat-value")]),
            ],
        ),
    ]


def _empty_figure(title: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        title=title,
        template="plotly_dark",
        paper_bgcolor="#0d1b4f",
        plot_bgcolor="#0d1b4f",
        font={"color": "#e8f1ff"},
        dragmode="pan",
        hovermode="x unified",
    )
    fig.update_xaxes(fixedrange=False, rangeslider_visible=False)
    fig.update_yaxes(fixedrange=False)
    return fig


def _safe_range_key(value, default="1D") -> str:
    value = str(value or default).upper()
    if value in {"1D", "1W", "1M", "3M", "1Y", "5Y", "MAX"}:
        return value
    return default


def _range_key_from_button(trigger_id: str | None, prefix: str, default="1D") -> str:
    if not trigger_id:
        return default

    raw = trigger_id.replace(prefix, "").lower()
    mapping = {
        "1d": "1D",
        "1w": "1W",
        "1m": "1M",
        "3m": "3M",
        "1y": "1Y",
        "5y": "5Y",
        "max": "MAX",
    }
    return mapping.get(raw, default)


def _clean_relayout_range(relayout_data):
    """
    Extract user-driven Plotly x/y ranges.

    Double-click reset/autorange returns live mode.
    Initial noise is ignored.
    """
    if not relayout_data:
        return no_update

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

    return no_update


def _clean_bars_for_view(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    df = bars.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time", "high", "low"])

    return df


def _visible_window_from_bars(bars: pd.DataFrame, range_key: str):
    df = _clean_bars_for_view(bars)
    if df.empty:
        return None

    range_key = _safe_range_key(range_key)
    end_time = df["time"].max()

    if range_key == "MAX":
        start_time = df["time"].min()
    else:
        days = RANGE_DAYS.get(range_key, 1)
        start_time = end_time - timedelta(days=days)
        start_time = max(start_time, df["time"].min())

    return [start_time, end_time]


def _fit_y_axis_to_visible_bars(fig, bars: pd.DataFrame, x_range=None):
    """
    Fit y-axis only to visible candles. This prevents candles from becoming
    long, flat, or unreadable when Plotly preserves a bad y-axis range.
    """
    df = _clean_bars_for_view(bars)
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


def _apply_chart_view(fig, bars: pd.DataFrame, chart_state: dict | None, default_range="1D"):
    state = chart_state or {}
    mode = state.get("mode", "live")
    range_key = _safe_range_key(state.get("range_key"), default_range)

    if bars is None or bars.empty:
        return fig

    if mode == "manual":
        x_range = state.get("x_range")
        y_range = state.get("y_range")

        if x_range:
            fig.update_xaxes(range=x_range, fixedrange=False)
            fig = _fit_y_axis_to_visible_bars(fig, bars, x_range)

        if y_range:
            fig.update_yaxes(range=y_range, fixedrange=False)

        return fig

    x_range = _visible_window_from_bars(bars, range_key)
    if x_range:
        fig.update_xaxes(range=x_range, fixedrange=False)

    fig = _fit_y_axis_to_visible_bars(fig, bars, x_range)
    return fig

def _is_today_or_latest_replay_date(replay_date) -> bool:
    """
    Live market paper trading should only be available when the Watch tab
    is using today's date or no date/latest mode.
    """
    if not replay_date:
        return True

    try:
        selected = pd.to_datetime(replay_date, errors="coerce")
        if pd.isna(selected):
            return False

        return selected.date() == datetime.now().date()
    except Exception:
        return False

def _default_chart_state(range_key="1D"):
    return {
        "mode": "live",
        "range_key": range_key,
        "x_range": None,
        "y_range": None,
    }

def register_callbacks(
        app,
        rt,
        replay_service,
        symbol_options,
        timeframe_map,
        paper_trading_service=None,
        paper_state_cache=None,
):
    strategy_engine = StrategyEngine()
    strategy_overlay_service = StrategyOverlayService()
    bar_view_service = BarViewService()
    chart_viewport_service = ChartViewportService()
    watch_chart_renderer = WatchChartRenderer()
    strategy_overlay_renderer = StrategyOverlayRenderer(
        replay_max_bars=160,
        replay_max_signals=25,
        slow_log_ms=120,
    )
    backtest_engine = BackTestEngine()

    # Strategy overlays are cached by StrategyOverlayService.

    def _trading_days_between(start_date, end_date):
        start = pd.to_datetime(start_date, errors="coerce")
        end = pd.to_datetime(end_date, errors="coerce")

        if pd.isna(start) or pd.isna(end):
            return []

        if end < start:
            start, end = end, start

        days = []
        current = start.normalize()

        while current <= end.normalize():
            if int(current.weekday()) < 5:
                days.append(current.date().isoformat())

            current = current + pd.Timedelta(days=1)

        return days

    def _strategy_docs_dir() -> Path:
        return Path(__file__).resolve().parent / "docs"

    def _strategy_examples_dir() -> Path:
        return _strategy_docs_dir() / "strategy_examples"

    def _read_strategy_doc_file(path: Path, fallback: str) -> str:
        try:
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"[STRATEGY DOC READ ERROR] {path}: {exc}", flush=True)

        return fallback

    def _read_strategy_example(example_file: str) -> str:
        safe_name = Path(str(example_file or "ema_crossover.txt")).name
        path = _strategy_examples_dir() / safe_name

        fallback = (
            "fast = ema(close, 9)\n"
            "slow = ema(close, 21)\n\n"
            "plot fast\n"
            "plot slow\n\n"
            "buy when crossover(fast, slow)\n"
            "sell when crossunder(fast, slow)\n"
        )

        return _read_strategy_doc_file(path, fallback).strip()

    @app.callback(
        Output("pair-title", "children"),
        Input("active-symbol", "data"),
        Input("main-tabs", "value"),
        Input("watch-symbol-dropdown", "value"),
        Input("symbol-dropdown", "value"),
        State("watch-state", "data"),
        State("dashboard-state", "data"),
    )
    def update_pair_title(
            active_symbol,
            active_tab,
            watch_symbol,
            dashboard_symbol_dropdown,
            watch_state,
            dashboard_state,
    ):
        if active_tab == "watch":
            symbol = (
                    watch_symbol
                    or (watch_state or {}).get("symbol")
                    or DEFAULT_SYMBOL
            )
        else:
            symbol = (
                    active_symbol
                    or dashboard_symbol_dropdown
                    or (dashboard_state or {}).get("symbol")
                    or DEFAULT_SYMBOL
            )

        symbol = str(symbol).upper().strip()
        company = rt.get_company_name(symbol)
        return f"{symbol} / {company}"

    @app.callback(
        Output("load-status-text", "children"),
        Input("load-status", "data"),
    )
    def show_load_status(status):
        return status

    @app.callback(
        Output("dashboard-state", "data"),
        Input("symbol-dropdown", "value"),
        Input("timeframe-dropdown", "value"),
        State("dashboard-state", "data"),
        prevent_initial_call=True,
    )
    def save_dashboard_state(symbol, timeframe, current_state):
        state = dict(current_state or {})
        if symbol:
            state["symbol"] = symbol
        if timeframe:
            state["timeframe"] = timeframe
        return state

    @app.callback(
        Output("watch-state", "data"),
        Input("watch-symbol-dropdown", "value"),
        Input("replay-speed", "value"),
        Input("replay-slider", "value"),
        Input("replay-date", "date"),
        State("watch-state", "data"),
        prevent_initial_call=True,
    )
    def save_watch_state(symbol, replay_speed, replay_index, replay_date, current_state):
        state = dict(current_state or {})
        if symbol:
            state["symbol"] = symbol
        if replay_speed is not None:
            state["replay_speed"] = replay_speed
        if replay_index is not None:
            state["replay_index"] = replay_index
        state["replay_date"] = replay_date
        return state

    @app.callback(
        Output("active-symbol", "data"),
        Output("load-status", "data"),
        Input("symbol-dropdown", "value"),
        State("active-symbol", "data"),
        prevent_initial_call=True,
    )
    def auto_load_symbol(symbol, current_active_symbol):
        if not symbol:
            return no_update, "No symbol selected"

        try:
            symbol = rt._sanitize_symbol(symbol)

            if symbol == current_active_symbol:
                return no_update, no_update

            rt.request_symbol(symbol)
            return symbol, f"Loading live data for {symbol}"
        except Exception as exc:
            return no_update, f"Error: {exc}"
    # ------------------------------------------------------------
    # Dashboard chart interaction state
    # ------------------------------------------------------------
    @app.callback(
        Output("dashboard-chart-state", "data"),
        Input("dashboard-live-mode", "n_clicks"),
        Input("dashboard-reset-view", "n_clicks"),
        Input("dashboard-range-1d", "n_clicks"),
        Input("dashboard-range-1w", "n_clicks"),
        Input("dashboard-range-1m", "n_clicks"),
        Input("dashboard-range-3m", "n_clicks"),
        Input("dashboard-range-1y", "n_clicks"),
        Input("dashboard-range-5y", "n_clicks"),
        Input("dashboard-range-max", "n_clicks"),
        Input("live-chart", "relayoutData"),
        State("dashboard-chart-state", "data"),
        prevent_initial_call=True,
    )
    def update_dashboard_chart_state(*args):
        current_state = dict(args[-1] or _default_chart_state())
        relayout_data = args[-2]
        trigger_id = ctx.triggered_id

        if trigger_id in {"dashboard-live-mode", "dashboard-reset-view"}:
            return _default_chart_state(current_state.get("range_key", "1D"))

        if isinstance(trigger_id, str) and trigger_id.startswith("dashboard-range-"):
            range_key = _range_key_from_button(trigger_id, "dashboard-range-", "1D")
            return _default_chart_state(range_key)

        if trigger_id == "live-chart":
            parsed = _clean_relayout_range(relayout_data)
            if parsed is no_update:
                return no_update

            new_state = dict(current_state)
            new_state.update(parsed)
            new_state["range_key"] = current_state.get("range_key", "1D")
            return new_state

        return no_update

    # ------------------------------------------------------------
    # Watch chart interaction state
    # ------------------------------------------------------------
    @app.callback(
        Output("watch-chart-state", "data"),
        Input("watch-live-mode", "n_clicks"),
        Input("watch-reset-view", "n_clicks"),
        Input("watch-range-1d", "n_clicks"),
        Input("watch-range-1w", "n_clicks"),
        Input("watch-range-1m", "n_clicks"),
        Input("watch-range-3m", "n_clicks"),
        Input("watch-range-1y", "n_clicks"),
        Input("watch-range-5y", "n_clicks"),
        Input("watch-range-max", "n_clicks"),
        Input("watch-chart", "relayoutData"),
        State("watch-chart-state", "data"),
        prevent_initial_call=True,
    )
    def update_watch_chart_state(*args):
        current_state = dict(args[-1] or _default_chart_state())
        relayout_data = args[-2]
        trigger_id = ctx.triggered_id

        if trigger_id in {"watch-live-mode", "watch-reset-view"}:
            return _default_chart_state(current_state.get("range_key", "1D"))

        if isinstance(trigger_id, str) and trigger_id.startswith("watch-range-"):
            range_key = _range_key_from_button(trigger_id, "watch-range-", "1D")
            return _default_chart_state(range_key)

        if trigger_id == "watch-chart":
            parsed = _clean_relayout_range(relayout_data)
            if parsed is no_update:
                return no_update

            new_state = dict(current_state)
            new_state.update(parsed)
            new_state["range_key"] = current_state.get("range_key", "1D")
            return new_state

        return no_update

    # ------------------------------------------------------------
    # Watch replay loading/control/clock
    # ------------------------------------------------------------
    app.clientside_callback(
        """
        function(activeTab, symbol, replayDate, loadRangeClicks, replayEndDate, timeframe, currentRequest) {
            if (activeTab !== "watch") {
                return [
                    dash_clientside.no_update,
                    dash_clientside.no_update
                ];
            }

            const ctx = dash_clientside.callback_context;
            let trigger = null;

            if (ctx && ctx.triggered && ctx.triggered.length > 0) {
                trigger = ctx.triggered[0].prop_id.split(".")[0];
            }

            // The interval dropdown is intentionally a State, not an Input.
            // Changing interval should redraw/resample the chart, not reload IB data.
            const req = currentRequest || {};
            const nonce = (req.nonce || 0) + 1;
            const mode = trigger === "replay-load-range" ? "range" : "single";

            return [
                "watch-loading-overlay",
                {
                    nonce: nonce,
                    symbol: symbol || "MSFT",
                    replay_date: replayDate || null,
                    replay_end_date: replayEndDate || replayDate || null,
                    timeframe: timeframe || "1 min",
                    load_mode: mode
                }
            ];
        }
        """,
        Output("watch-loading-overlay", "className", allow_duplicate=True),
        Output("watch-load-request", "data", allow_duplicate=True),
        Input("main-tabs", "value"),
        Input("watch-symbol-dropdown", "value"),
        Input("replay-date", "date"),
        Input("replay-load-range", "n_clicks"),
        State("replay-end-date", "date"),
        State("watch-timeframe-dropdown", "value"),
        State("watch-load-request", "data"),
        prevent_initial_call=True,
    )

    # ------------------------------------------------------------------
    # WATCH REPLAY SERVER LOADER - SINGLE DAY + STITCHED DATE RANGE
    # ------------------------------------------------------------------


    @app.callback(
        Output("watch-status", "children", allow_duplicate=True),
        Output("replay-slider", "max", allow_duplicate=True),
        Output("replay-slider", "value", allow_duplicate=True),
        Output("watch-loading-overlay", "className", allow_duplicate=True),
        Output("replay-render-trigger", "data", allow_duplicate=True),
        Input("watch-load-request", "data"),
        State("replay-speed", "value"),
        State("main-tabs", "value"),
        State("replay-render-trigger", "data"),
        prevent_initial_call=True,
    )
    def load_watch_symbol_from_request(load_request, replay_speed, active_tab, render_trigger):
        if active_tab != "watch":
            return no_update, no_update, no_update, no_update, no_update

        if not load_request:
            return no_update, no_update, no_update, no_update, no_update

        symbol = (load_request.get("symbol") or DEFAULT_SYMBOL).upper().strip()
        replay_date = load_request.get("replay_date")
        replay_end_date = load_request.get("replay_end_date") or replay_date
        display_timeframe = load_request.get("timeframe") or "1 min"
        load_mode = load_request.get("load_mode") or "single"

        try:
            if load_mode == "range":
                trading_days = _trading_days_between(replay_date, replay_end_date)

                if not trading_days:
                    render_trigger = int(render_trigger or 0) + 1
                    return (
                        "No weekday trading days found in selected replay range.",
                        100,
                        1,
                        "watch-loading-overlay hidden",
                        render_trigger,
                    )

                stitched = replay_service.load_date_range(
                    symbol=symbol,
                    start_date=replay_date,
                    end_date=replay_end_date,
                    timeframe="1 min",
                    speed=replay_speed or 1,
                )

                info = replay_service.info()
                max_idx = max(1, int(info.get("max_index", len(stitched))))
                idx = max(1, int(info.get("current_index", 1)))
                render_trigger = int(render_trigger or 0) + 1

                return (
                    (
                        f"Loaded {symbol} replay range {replay_date} → {replay_end_date} · "
                        f"{len(trading_days)} trading days · {len(stitched):,} raw 1-min bars · "
                        f"display {display_timeframe}."
                    ),
                    max_idx,
                    idx,
                    "watch-loading-overlay hidden",
                    render_trigger,
                )

            # Single-day replay loading also uses raw 1-minute bars.
            # The Watch Interval dropdown only resamples display data.
            status, info = replay_service.load_replay(
                symbol=symbol,
                timeframe="1 min",
                replay_date=replay_date,
                speed=replay_speed or 1,
            )

            max_idx = max(1, int(info.get("max_index", 1)))
            idx = max(1, int(info.get("current_index", 1)))
            render_trigger = int(render_trigger or 0) + 1

            return (
                f"{status} · display {display_timeframe}",
                max_idx,
                idx,
                "watch-loading-overlay hidden",
                render_trigger,
            )

        except Exception as exc:
            print(f"[REPLAY LOAD ERROR] {exc}", flush=True)
            render_trigger = int(render_trigger or 0) + 1

            return (
                f"Replay load error: {exc}",
                100,
                1,
                "watch-loading-overlay hidden",
                render_trigger,
            )

    @app.callback(
        Output("watch-status", "children", allow_duplicate=True),
        Input("replay-speed", "value"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def update_replay_speed(speed, active_tab):
        if active_tab != "watch":
            return no_update

        try:
            replay_service.set_speed(speed or 1)
            return f"Replay speed set to {speed or 1}x"
        except Exception as exc:
            return f"Replay speed error: {exc}"

    @app.callback(
        Output("watch-status", "children", allow_duplicate=True),
        Output("replay-render-trigger", "data", allow_duplicate=True),
        Input("replay-play", "n_clicks"),
        Input("replay-pause", "n_clicks"),
        Input("replay-step", "n_clicks"),
        Input("replay-rewind", "n_clicks"),
        State("replay-render-trigger", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def control_replay(
            play_clicks,
            pause_clicks,
            step_clicks,
            rewind_clicks,
            render_trigger,
            active_tab,
    ):
        if active_tab != "watch":
            return no_update, no_update

        trigger = ctx.triggered_id
        render_trigger = int(render_trigger or 0)

        try:
            info = replay_service.info()
            max_index = max(1, int(info.get("max_index", 1)))

            if max_index <= 1:
                return "No replay data loaded.", no_update

            if trigger == "replay-play":
                replay_service.play()
                return "Replay playing", render_trigger + 1

            if trigger == "replay-pause":
                replay_service.pause()
                return "Replay paused", render_trigger + 1

            if trigger == "replay-step":
                replay_service.forward(1)
                idx = max(1, int(replay_service.info().get("current_index", 1)))
                return f"Replay stepped to {idx}", render_trigger + 1

            if trigger == "replay-rewind":
                replay_service.rewind(1)
                idx = max(1, int(replay_service.info().get("current_index", 1)))
                return f"Replay rewound to {idx}", render_trigger + 1

                # Ignore programmatic slider updates from render_watch_tab.
                # Only treat it as user input when the value actually changes.
                if idx == current_idx:
                    return no_update, no_update

                replay_service.set_index(idx)
                return f"Replay moved to {idx}", render_trigger + 1

            return no_update, no_update

        except Exception as exc:
            print(f"[REPLAY CONTROL ERROR] {exc}", flush=True)
            return f"Replay control error: {exc}", no_update

    @app.callback(
        Output("watch-status", "children", allow_duplicate=True),
        Output("replay-render-trigger", "data", allow_duplicate=True),
        Input("replay-slider", "value"),
        State("replay-render-trigger", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def seek_replay_from_slider(slider_value, render_trigger, active_tab):
        """
        Manual replay seek.

        Moving the slider should immediately move the replay cursor and redraw the
        chart, even while replay is paused. This keeps user control separate from
        Play/Pause/Step button logic.
        """
        if active_tab != "watch":
            return no_update, no_update

        if slider_value is None:
            return no_update, no_update

        try:
            info = replay_service.info()
            max_index = max(1, int(info.get("max_index", 1) or 1))
            current_idx = max(1, int(info.get("current_index", 1) or 1))

            idx = max(1, min(int(slider_value or 1), max_index))

            if idx == current_idx:
                return no_update, no_update

            # Manual slider movement should give the user control.
            # Pause playback so the clock does not immediately fight the seek.
            try:
                replay_service.pause()
            except Exception:
                pass

            replay_service.set_index(idx)

            return (
                f"Replay moved to {idx:,} / {max_index:,}",
                int(render_trigger or 0) + 1,
            )

        except Exception as exc:
            print(f"[REPLAY SLIDER SEEK ERROR] {exc}", flush=True)
            return f"Replay slider error: {exc}", no_update
        
    @app.callback(
        Output("replay-render-trigger", "data", allow_duplicate=True),
        Input("replay-clock", "n_intervals"),
        State("replay-render-trigger", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def advance_replay_clock(_n, render_trigger, active_tab):
        if active_tab != "watch":
            return no_update

        try:
            info_before = replay_service.info()

            if not info_before.get("playing"):
                return no_update

            before_idx = max(1, int(info_before.get("current_index", 1) or 1))
            before_max_idx = max(1, int(info_before.get("max_index", 1) or 1))

            replay_service.tick()

            info_after = replay_service.info()
            after_idx = max(1, int(info_after.get("current_index", 1) or 1))
            after_max_idx = max(1, int(info_after.get("max_index", before_max_idx) or before_max_idx))

            # End-of-replay guard:
            # Some replay engines leave `playing=True` at the final cursor.
            # Force a pause and one final render so full overlays/backgrounds
            # and paper markers are restored at the end of playback.
            if after_idx >= after_max_idx:
                try:
                    replay_service.pause()
                except Exception:
                    pass
                return int(render_trigger or 0) + 1

            playing_before = bool(info_before.get("playing"))
            playing_after = bool(info_after.get("playing"))

            if after_idx != before_idx or playing_before != playing_after:
                return int(render_trigger or 0) + 1

            return no_update

        except Exception as exc:
            print(f"[REPLAY CLOCK ERROR] {exc}", flush=True)
            return no_update

    @app.callback(
        Output("trade-analytics-content", "children"),
        Input("watch-workspace-tabs", "value"),
        Input("paper-trade-trigger", "data"),
        Input("replay-render-trigger", "data"),
        State("watch-symbol-dropdown", "value"),
        State("paper-price-source", "value"),
        State("main-tabs", "value"),
        prevent_initial_call=False,
    )
    def render_trade_analytics_content(
            workspace_tab,
            _paper_trigger,
            _replay_trigger,
            symbol,
            price_source,
            active_tab,
    ):
        if active_tab != "watch":
            return no_update

        if workspace_tab not in ("trade-analytics", None):
            return no_update

        symbol = (symbol or DEFAULT_SYMBOL).upper().strip()
        price_source = str(price_source or "replay").lower().strip()

        if paper_trading_service is None:
            return html.Div("Paper trading service is disabled.", className="paper-empty")

        try:
            positions = paper_trading_service.positions_df()
        except Exception:
            positions = pd.DataFrame()

        try:
            orders = paper_trading_service.orders_df()
        except Exception:
            orders = pd.DataFrame()

        try:
            fills = paper_trading_service.fills_df()
        except Exception:
            fills = pd.DataFrame()

        try:
            price, _timestamp = _paper_current_price_and_time(symbol, price_source)
        except Exception:
            price = None

        try:
            if paper_state_cache is not None:
                prices = {symbol: float(price)} if price is not None else {}
                paper_state_cache.save_from_service(
                    paper_trading_service,
                    prices=prices,
                )
        except Exception as cache_exc:
            print(f"[ANALYTICS CACHE SAVE ERROR] {cache_exc}", flush=True)

        if fills is None:
            fills = pd.DataFrame()

        if positions is None:
            positions = pd.DataFrame()

        if orders is None:
            orders = pd.DataFrame()

        symbol_fills = fills.copy()
        if not symbol_fills.empty and "symbol" in symbol_fills.columns:
            symbol_fills = symbol_fills[
                symbol_fills["symbol"].astype(str).str.upper() == symbol
                ].copy()

        symbol_positions = positions.copy()
        if not symbol_positions.empty and "symbol" in symbol_positions.columns:
            symbol_positions = symbol_positions[
                symbol_positions["symbol"].astype(str).str.upper() == symbol
                ].copy()

        symbol_orders = orders.copy()
        if not symbol_orders.empty and "symbol" in symbol_orders.columns:
            symbol_orders = symbol_orders[
                symbol_orders["symbol"].astype(str).str.upper() == symbol
                ].copy()

        total_fills = int(len(symbol_fills))

        realized_pnl = 0.0
        if not symbol_fills.empty and "realized_pnl" in symbol_fills.columns:
            realized_pnl = float(
                pd.to_numeric(symbol_fills["realized_pnl"], errors="coerce")
                .fillna(0.0)
                .sum()
            )

        buy_count = 0
        sell_count = 0
        side_text = "No fills yet"

        if not symbol_fills.empty and "side" in symbol_fills.columns:
            sides = symbol_fills["side"].astype(str).str.upper()
            buy_count = int((sides == "BUY").sum())
            sell_count = int((sides == "SELL").sum())
            side_text = f"BUY {buy_count} · SELL {sell_count}"

        open_qty = 0.0
        if not symbol_positions.empty and "quantity" in symbol_positions.columns:
            open_qty = float(
                pd.to_numeric(symbol_positions["quantity"], errors="coerce")
                .fillna(0.0)
                .sum()
            )

        position_side = "Flat"
        if open_qty > 0:
            position_side = "Long"
        elif open_qty < 0:
            position_side = "Short"

        pnl_class = (
            "analytics-value analytics-positive"
            if realized_pnl >= 0
            else "analytics-value analytics-negative"
        )

        cards = html.Div(
            className="analytics-card-grid",
            children=[
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Symbol", className="analytics-label"),
                        html.Div(symbol, className="analytics-value"),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Position", className="analytics-label"),
                        html.Div(
                            f"{position_side} {abs(open_qty):g}",
                            className="analytics-value",
                        ),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Realized PnL", className="analytics-label"),
                        html.Div(f"${realized_pnl:,.2f}", className=pnl_class),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Fills", className="analytics-label"),
                        html.Div(f"{total_fills} · {side_text}", className="analytics-value"),
                    ],
                ),
            ],
        )

        def _build_pnl_chart(fills_df):
            fig = go.Figure()

            fig.update_layout(
                title="Cumulative Realized PnL",
                template="plotly_dark",
                paper_bgcolor="#10101a",
                plot_bgcolor="#0a0a12",
                font={"color": "#f1f1f8"},
                height=280,
                margin=dict(l=42, r=24, t=50, b=38),
                showlegend=False,
            )

            if fills_df is None or fills_df.empty or "realized_pnl" not in fills_df.columns:
                return dcc.Graph(
                    figure=fig,
                    config={"displayModeBar": False, "responsive": True},
                    className="analytics-pnl-chart",
                )

            df = fills_df.copy()

            time_col = None
            for candidate in ["timestamp", "filled_at", "submitted_at"]:
                if candidate in df.columns:
                    time_col = candidate
                    break

            if time_col is None:
                df["_time"] = range(len(df))
                time_col = "_time"
            else:
                df[time_col] = pd.to_datetime(
                    df[time_col],
                    errors="coerce",
                    format="mixed",
                )
                df = df.dropna(subset=[time_col]).copy()

            if df.empty:
                return dcc.Graph(
                    figure=fig,
                    config={"displayModeBar": False, "responsive": True},
                    className="analytics-pnl-chart",
                )

            df["realized_pnl"] = pd.to_numeric(
                df["realized_pnl"],
                errors="coerce",
            ).fillna(0.0)

            df = df.sort_values(time_col).copy()
            df["cumulative_pnl"] = df["realized_pnl"].cumsum()

            final_pnl = float(df["cumulative_pnl"].iloc[-1])
            line_color = "#34d399" if final_pnl >= 0 else "#ff6b6b"

            fig.add_trace(
                go.Scatter(
                    x=df[time_col],
                    y=df["cumulative_pnl"],
                    mode="lines+markers",
                    line=dict(width=3, color=line_color),
                    marker=dict(size=7, color=line_color),
                    name="Cumulative Realized PnL",
                    hovertemplate=(
                        "Time: %{x}<br>"
                        "Cumulative PnL: $%{y:,.2f}"
                        "<extra></extra>"
                    ),
                )
            )

            fig.add_hline(
                y=0,
                line_width=1,
                line_dash="dot",
                line_color="rgba(148, 163, 184, 0.7)",
            )

            fig.update_layout(
                title=f"Cumulative Realized PnL: ${final_pnl:,.2f}",
                hovermode="x unified",
            )

            fig.update_xaxes(
                showgrid=True,
                gridcolor="rgba(255,255,255,0.06)",
            )

            fig.update_yaxes(
                title="PnL $",
                showgrid=True,
                gridcolor="rgba(255,255,255,0.06)",
                zeroline=False,
            )

            return dcc.Graph(
                figure=fig,
                config={"displayModeBar": False, "responsive": True},
                className="analytics-pnl-chart",
            )

        def _pre_from_df(df, empty_text, max_rows=12):
            if df is None or df.empty:
                return html.Div(empty_text, className="paper-empty")

            view = df.tail(max_rows).copy()

            for col in view.columns:
                col_lower = str(col).lower()
                if col_lower in {"timestamp", "submitted_at", "filled_at"}:
                    try:
                        view[col] = pd.to_datetime(
                            view[col],
                            errors="coerce",
                            format="mixed",
                        ).dt.strftime("%Y-%m-%d %H:%M:%S")
                        view[col] = view[col].fillna("")
                    except Exception:
                        pass

            return html.Pre(
                view.to_string(index=False),
                className="analytics-table",
            )

        return html.Div(
            children=[
                         cards,

                         html.Div("PnL Curve", className="analytics-section-title"),
                         _build_pnl_chart(symbol_fills),

                         html.Div("Open Position", className="analytics-section-title"),
                         _pre_from_df(symbol_positions, "No open position."),

                         html.Div("Recent Orders", className="analytics-section-title"),
                         _pre_from_df(symbol_orders, "No orders yet."),

                         html.Div("Recent Fills", className="analytics-section-title"),
                         _pre_from_df(symbol_fills, "No fills yet."),
                     ]
        )

    def _build_backtest_equity_graph(equity_curve, initial_cash=100000):
        fig = go.Figure()

        fig.update_layout(
            title="Backtest Cumulative PnL",
            template="plotly_dark",
            paper_bgcolor="#10101a",
            plot_bgcolor="#0a0a12",
            font={"color": "#f1f1f8"},
            height=300,
            margin=dict(l=42, r=24, t=50, b=38),
            showlegend=False,
            hovermode="x unified",
        )

        if equity_curve is not None and not equity_curve.empty and "equity" in equity_curve.columns:
            df = equity_curve.copy()

            df["equity"] = pd.to_numeric(df["equity"], errors="coerce")
            df = df.dropna(subset=["equity"]).copy()

            if not df.empty:
                x_values = df["time"] if "time" in df.columns else df.index
                df["cumulative_pnl"] = df["equity"] - float(initial_cash)

                final_pnl = float(df["cumulative_pnl"].iloc[-1])
                line_color = "#34d399" if final_pnl >= 0 else "#ff6b6b"

                fig.add_trace(
                    go.Scatter(
                        x=x_values,
                        y=df["cumulative_pnl"],
                        mode="lines",
                        name="Cumulative PnL",
                        line=dict(width=3, color=line_color),
                        hovertemplate=(
                            "Time: %{x}<br>"
                            "PnL: $%{y:,.2f}"
                            "<extra></extra>"
                        ),
                    )
                )

                fig.add_hline(
                    y=0,
                    line_width=1,
                    line_dash="dot",
                    line_color="rgba(148, 163, 184, 0.7)",
                )

                fig.update_layout(
                    title=f"Backtest Cumulative PnL: ${final_pnl:,.2f}",
                )

                fig.update_yaxes(
                    title="PnL $",
                    showgrid=True,
                    gridcolor="rgba(255,255,255,0.06)",
                    zeroline=False,
                )

                fig.update_xaxes(
                    showgrid=True,
                    gridcolor="rgba(255,255,255,0.06)",
                )

        return dcc.Graph(
            figure=fig,
            config={"displayModeBar": False, "responsive": True},
            className="analytics-pnl-chart",
        )

    def _build_backtest_trades_table(trades):
        if not trades:
            return html.Div("No completed trades.", className="paper-empty")

        rows = []

        for trade in trades[-20:]:
            rows.append(
                {
                    "Entry Time": trade.entry_time,
                    "Exit Time": trade.exit_time,
                    "Entry": round(float(trade.entry_price), 4),
                    "Exit": round(float(trade.exit_price), 4),
                    "Qty": int(trade.quantity),
                    "PnL": round(float(trade.pnl), 2),
                    "Return %": round(float(trade.return_pct), 2),
                    "Bars": int(trade.bars_held),
                }
            )

        df = pd.DataFrame(rows)

        return html.Pre(
            df.to_string(index=False),
            className="analytics-table",
        )

    def _build_backtest_results_panel(backtest_result):
        if backtest_result is None:
            return html.Div("No backtest result.", className="paper-empty")

        pnl_class = (
            "analytics-value analytics-positive"
            if backtest_result.total_pnl >= 0
            else "analytics-value analytics-negative"
        )

        cards = html.Div(
            className="analytics-card-grid backtest-card-grid",
            children=[
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Final Equity", className="analytics-label"),
                        html.Div(
                            f"${backtest_result.final_equity:,.2f}",
                            className="analytics-value",
                        ),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Total PnL", className="analytics-label"),
                        html.Div(
                            f"${backtest_result.total_pnl:,.2f}",
                            className=pnl_class,
                        ),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Return", className="analytics-label"),
                        html.Div(
                            f"{backtest_result.total_return_pct:,.2f}%",
                            className=pnl_class,
                        ),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Max Drawdown", className="analytics-label"),
                        html.Div(
                            f"{backtest_result.max_drawdown_pct:,.2f}%",
                            className="analytics-value analytics-negative",
                        ),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Trades", className="analytics-label"),
                        html.Div(
                            str(backtest_result.trade_count),
                            className="analytics-value",
                        ),
                    ],
                ),
                html.Div(
                    className="analytics-card",
                    children=[
                        html.Div("Win Rate", className="analytics-label"),
                        html.Div(
                            f"{backtest_result.win_rate_pct:,.2f}%",
                            className="analytics-value",
                        ),
                    ],
                ),
            ],
        )

        errors = []

        if backtest_result.errors:
            errors = [
                html.Div("Backtest Notes", className="analytics-section-title"),
                html.Pre(
                    "\n".join(backtest_result.errors),
                    className="analytics-table",
                ),
            ]

        return html.Div(
            children=[
                cards,
                html.Div("Cumulative PnL", className="analytics-section-title"),
                _build_backtest_equity_graph(
                    backtest_result.equity_curve,
                    backtest_result.initial_cash,
                ),
                html.Div("Trades", className="analytics-section-title"),
                _build_backtest_trades_table(backtest_result.trades),
                *errors,
            ]
        )

    def _get_backtest_bars():
        """
        Prefer the full loaded replay dataset for backtests.
        Fall back to visible bars if the replay service does not expose full bars.
        """

        method_names = [
            "all_bars",
            "full_bars",
            "loaded_bars",
            "bars_df",
            "get_all_bars",
            "get_full_bars",
        ]

        for name in method_names:
            obj = getattr(replay_service, name, None)

            if callable(obj):
                try:
                    bars = obj()

                    if bars is not None and not bars.empty:
                        return bars.copy()
                except Exception:
                    pass

        attr_names = [
            "bars",
            "_bars",
            "df",
            "_df",
            "data",
            "_data",
            "replay_bars",
            "_replay_bars",
            "loaded_df",
            "_loaded_df",
        ]

        for name in attr_names:
            try:
                bars = getattr(replay_service, name, None)

                if bars is not None and not bars.empty:
                    return bars.copy()
            except Exception:
                pass

        try:
            visible = replay_service.visible_bars()

            if visible is not None and not visible.empty:
                return visible.copy()
        except Exception:
            pass

        return pd.DataFrame()

    @app.callback(
        Output("strategy-script-input", "value", allow_duplicate=True),
        Output("strategy-status", "children", allow_duplicate=True),
        Input("strategy-insert-example", "n_clicks"),
        State("strategy-example-dropdown", "value"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def insert_strategy_example(n_clicks, example_file, active_tab):
        if active_tab != "watch":
            return no_update, no_update

        if not n_clicks:
            return no_update, no_update

        script = _read_strategy_example(example_file)
        safe_name = Path(str(example_file or "")).name

        if safe_name == "ema_supertrend.txt":
            return (
                script,
                (
                    "Inserted EMA + Supertrend planned example. "
                    "This example is documentation only until Strategy Language v0.2 supports "
                    "ta.supertrend, ta.ema, boolean expressions, and comparisons."
                ),
            )

        label = {
            "ema_crossover.txt": "EMA Crossover",
            "sma_fast_test.txt": "Fast SMA Test",
            "rsi_mean_reversion.txt": "RSI Mean Reversion",
        }.get(safe_name, safe_name or "example")

        return script, f"Inserted example: {label}"

    @app.callback(
        Output("strategy-help-content", "children"),
        Input("strategy-show-language-guide", "n_clicks"),
        Input("strategy-show-function-reference", "n_clicks"),
        Input("strategy-example-dropdown", "value"),
        State("main-tabs", "value"),
        prevent_initial_call=False,
    )
    def render_strategy_help_content(
            guide_clicks,
            reference_clicks,
            example_file,
            active_tab,
    ):
        if active_tab != "watch":
            return no_update

        trigger = ctx.triggered_id

        if trigger == "strategy-show-function-reference":
            try:
                markdown_text = get_function_reference_markdown()
            except Exception as exc:
                markdown_text = (
                    "# Function Reference\n\n"
                    "Could not load function registry.\n\n"
                    f"Error: `{exc}`"
                )

            return html.Div(
                className="strategy-help-markdown-card",
                children=[
                    dcc.Markdown(
                        markdown_text,
                        className="strategy-help-markdown",
                    ),
                ],
            )

        if trigger == "strategy-example-dropdown":
            example_text = _read_strategy_example(example_file)

            return html.Div(
                className="strategy-help-markdown-card",
                children=[
                    html.Div("Example Preview", className="analytics-section-title"),
                    html.Pre(
                        example_text,
                        className="strategy-example-preview",
                    ),
                ],
            )

        guide_path = _strategy_docs_dir() / "STRATEGY_LANGUAGE.md"

        guide_text = _read_strategy_doc_file(
            guide_path,
            fallback=(
                "# Strategy Language\n\n"
                "Could not find `Live/docs/STRATEGY_LANGUAGE.md`.\n\n"
                "Supported basic example:\n\n"
                "```text\n"
                "fast = ema(close, 9)\n"
                "slow = ema(close, 21)\n"
                "plot fast\n"
                "plot slow\n"
                "buy when crossover(fast, slow)\n"
                "sell when crossunder(fast, slow)\n"
                "```"
            ),
        )

        return html.Div(
            className="strategy-help-markdown-card",
            children=[
                dcc.Markdown(
                    guide_text,
                    className="strategy-help-markdown",
                ),
            ],
        )

    @app.callback(
        Output("backtest-status", "children"),
        Output("backtest-results-panel", "children"),
        Input("strategy-run-backtest", "n_clicks"),
        State("strategy-script-input", "value"),
        State("backtest-initial-cash", "value"),
        State("backtest-quantity", "value"),
        State("main-tabs", "value"),
        State("watch-symbol-dropdown", "value"),
        prevent_initial_call=True,
    )
    def run_strategy_backtest(
            n_clicks,
            script_text,
            initial_cash,
            quantity,
            active_tab,
            symbol,
    ):
        if active_tab != "watch":
            return no_update, no_update

        script_text = str(script_text or "").strip()
        symbol = (symbol or DEFAULT_SYMBOL).upper().strip()

        if not script_text:
            return (
                "No strategy script entered.",
                html.Div(
                    "Enter a strategy script first.",
                    className="paper-empty",
                ),
            )

        try:
            bars = _get_backtest_bars()

            if bars is None or bars.empty:
                return (
                    "No replay bars available.",
                    html.Div(
                        "Load replay data before running a backtest.",
                        className="paper-empty",
                    ),
                )

            if "time" in bars.columns and not bars.empty:
                print(
                    f"[BACKTEST DATA] using {len(bars):,} bars "
                    f"from {bars['time'].iloc[0]} to {bars['time'].iloc[-1]}",
                    flush=True,
                )
            else:
                print(f"[BACKTEST DATA] using {len(bars):,} bars", flush=True)

            strategy_result = strategy_engine.run(script_text, bars)

            if strategy_result.errors:
                return (
                    "Strategy script has errors.",
                    html.Div(
                        children=[
                            html.Div("Script Errors", className="analytics-section-title"),
                            html.Pre(
                                "\n".join(strategy_result.errors),
                                className="analytics-table",
                            ),
                        ]
                    ),
                )

            if not strategy_result.signals:
                return (
                    f"No strategy signals found for {symbol}. Bars checked: {len(bars):,}.",
                    html.Div(
                        [
                            html.Div("No Signals", className="analytics-section-title"),
                            html.Div(
                                "The script ran, but no buy/sell signals were generated. "
                                "Try a faster crossover like SMA 3 / SMA 8, move replay farther forward, "
                                "or backtest a larger loaded dataset.",
                                className="paper-empty",
                            ),
                        ]
                    ),
                )

            backtest_result = backtest_engine.run(
                bars=bars,
                signals=strategy_result.signals,
                initial_cash=initial_cash or 100000,
                quantity=quantity or 1,
            )

            return (
                (
                    f"Backtest complete for {symbol}. "
                    f"Bars: {len(bars):,} · "
                    f"Signals: {len(strategy_result.signals):,} · "
                    f"Trades: {backtest_result.trade_count:,}"
                ),
                _build_backtest_results_panel(backtest_result),
            )

        except Exception as exc:
            print(f"[BACKTEST ERROR] {exc}", flush=True)
            return (
                f"Backtest error: {exc}",
                html.Div(str(exc), className="paper-empty"),
            )

    def _resample_watch_bars(bars, timeframe):
        """
        Convert 1-minute replay/live bars into larger chart intervals for Watch.
        Replay still runs on the original data; this only changes chart display.
        """

        if bars is None or bars.empty:
            return pd.DataFrame()

        timeframe = str(timeframe or "1 min").lower().strip()

        rule_map = {
            "1 min": None,
            "1m": None,
            "5 min": "5min",
            "5m": "5min",
            "15 min": "15min",
            "15m": "15min",
            "30 min": "30min",
            "30m": "30min",
            "1 hour": "1h",
            "1h": "1h",
            "1 day": "1D",
            "1d": "1D",
        }

        rule = rule_map.get(timeframe)

        clean = bars.copy()

        if "time" not in clean.columns:
            return clean

        clean["time"] = pd.to_datetime(
            clean["time"],
            errors="coerce",
            format="mixed",
        )

        clean = clean.dropna(
            subset=["time", "open", "high", "low", "close"]
        ).copy()

        if clean.empty:
            return clean

        for col in ["open", "high", "low", "close"]:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

        if "volume" in clean.columns:
            clean["volume"] = pd.to_numeric(clean["volume"], errors="coerce").fillna(0)
        else:
            clean["volume"] = 0

        clean = clean.dropna(subset=["open", "high", "low", "close"]).copy()
        clean = clean.sort_values("time").copy()

        if rule is None:
            return clean.reset_index(drop=True)

        resampled = (
            clean.set_index("time")
            .resample(rule)
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )

        return resampled.reset_index(drop=True)

    # ------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------
    @app.callback(
        Output("quote-strip", "children"),
        Output("live-chart", "figure"),
        Output("dashboard-metrics-strip", "children"),
        Output("dashboard-stats-grid", "children"),
        Input("ui-interval", "n_intervals"),
        Input("active-symbol", "data"),
        Input("timeframe-dropdown", "value"),
        Input("dashboard-chart-state", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=False,
    )
    def render_dashboard_chart(
            _n,
            active_symbol,
            timeframe,
            dashboard_chart_state,
            active_tab,
    ):
        """
        Dashboard-only live renderer.

        This intentionally does NOT touch Watch/replay/paper callbacks.
        It requests the symbol defensively and waits cleanly if RealTimeIB
        has not created the loaded state yet.
        """
        if active_tab != "dashboard":
            return no_update, no_update, no_update, no_update

        try:
            symbol = (active_symbol or DEFAULT_SYMBOL).upper().strip()
            timeframe = timeframe or DEFAULT_TIMEFRAME
            company_name = rt.get_company_name(symbol)

            # Make sure the dashboard symbol has an active live subscription.
            # This is cheap if already subscribed.
            try:
                rt.request_symbol(symbol)
            except Exception as req_exc:
                print(f"[DASHBOARD REQUEST WARNING] {symbol}: {req_exc}", flush=True)

            # RealTimeIB can briefly lag after a symbol switch.
            # Instead of throwing "No loaded state", show a loading chart and retry
            # on the next ui-interval tick.
            try:
                snap = rt.get_snapshot(symbol, timeframe)
            except Exception as snapshot_exc:
                msg = str(snapshot_exc)

                if "No loaded state" in msg:
                    try:
                        rt.request_symbol(symbol)
                    except Exception:
                        pass

                    fig = _empty_figure(f"{symbol} | Loading live candles...")
                    quote_text = f"LIVE · {company_name} ({symbol}) · Loading live data..."

                    metrics = [
                        html.Div(f"{symbol} / {company_name}", className="metric-price"),
                        html.Div("Waiting for live candles...", className="metric-muted"),
                    ]

                    stats = [
                        html.Div(
                            className="stat-card",
                            children=[
                                html.Div("Waiting for bars...", className="stat-label")
                            ],
                        )
                    ]

                    return quote_text, fig, metrics, stats

                raise

            bars = snap.bars.copy() if snap.bars is not None else pd.DataFrame()

            if bars is not None and not bars.empty:
                bars["time"] = pd.to_datetime(
                    bars["time"],
                    errors="coerce",
                    format="mixed",
                )
                bars = bars.dropna(
                    subset=["time", "open", "high", "low", "close"]
                ).copy()

            if bars is None or bars.empty:
                fig = _empty_figure(f"{symbol} | Waiting for live candles...")
                quote_text = f"LIVE · {company_name} ({symbol}) · Waiting for candles"
                return quote_text, fig, [], []

            latest_time = str(bars.iloc[-1]["time"])
            latest_open = float(bars.iloc[-1]["open"])
            latest_high = float(bars.iloc[-1]["high"])
            latest_low = float(bars.iloc[-1]["low"])
            latest_close = float(bars.iloc[-1]["close"])
            current_price = float(snap.last) if snap.last is not None else latest_close

            print(
                f"[DASHBOARD DEBUG] tick={_n} {symbol} last={current_price} "
                f"bar_o={latest_open} bar_h={latest_high} "
                f"bar_l={latest_low} bar_c={latest_close} "
                f"bar_time={latest_time}",
                flush=True,
            )

            fig = create_candlestick_figure(
                bars,
                symbol,
                timeframe,
                current_price=current_price,
            )

            fig = _apply_chart_view(
                fig,
                bars,
                dashboard_chart_state,
                default_range="1D",
            )

            state = dashboard_chart_state or {}
            range_key = _safe_range_key(state.get("range_key"), "1D")
            mode = state.get("mode", "live")

            try:
                if fig.data:
                    for trace in fig.data:
                        if hasattr(trace, "x") and trace.x is not None:
                            trace.x = list(trace.x)
                        if hasattr(trace, "open") and trace.open is not None:
                            trace.open = [float(x) for x in trace.open]
                        if hasattr(trace, "high") and trace.high is not None:
                            trace.high = [float(x) for x in trace.high]
                        if hasattr(trace, "low") and trace.low is not None:
                            trace.low = [float(x) for x in trace.low]
                        if hasattr(trace, "close") and trace.close is not None:
                            trace.close = [float(x) for x in trace.close]
            except Exception as trace_exc:
                print(f"[DASHBOARD TRACE NORMALIZE WARNING] {trace_exc}", flush=True)

            redraw_key = (
                f"{symbol}-{timeframe}-{mode}-{range_key}-"
                f"{latest_time}-{latest_open}-{latest_high}-{latest_low}-{latest_close}-"
                f"{current_price}-{_n}"
            )

            fig.update_layout(
                uirevision=None,
                datarevision=redraw_key,
                dragmode="pan",
                title={
                    "text": f"{symbol} · {timeframe} · Last {current_price:,.2f} · tick {_n}",
                    "x": 0.02,
                    "xanchor": "left",
                },
            )

            updated = snap.updated_at.strftime("%H:%M:%S") if snap.updated_at else "--:--:--"
            quote_text = (
                f"LIVE · {company_name} ({symbol}) · Updated {updated} · "
                f"Last {current_price:,.2f}"
            )

            open_val = float(bars.iloc[0]["open"])

            metrics = _build_metrics_strip(
                symbol,
                company_name,
                current_price,
                open_val,
                snap.updated_at,
            )

            stats = _build_stats_grid_from_bars(bars)

            return quote_text, fig, metrics, stats

        except Exception as exc:
            print(f"[DASHBOARD RENDER ERROR] {exc}", flush=True)
            fig = _empty_figure(f"Loading dashboard... {exc}")
            return f"Loading dashboard... {exc}", fig, [], []


    # ------------------------------------------------------------
    # Watch chart render
    # ------------------------------------------------------------
    @app.callback(
        Output("watch-chart", "figure"),
        Input("replay-render-trigger", "data"),
        Input("watch-load-request", "data"),
        Input("watch-chart-state", "data"),
        Input("paper-trade-trigger", "data"),
        Input("strategy-script-store", "data"),
        Input("watch-timeframe-dropdown", "value"),
        Input("ui-interval", "n_intervals"),
        State("main-tabs", "value"),
        State("watch-symbol-dropdown", "value"),
        State("paper-price-source", "value"),
        State("replay-date", "date"),
        prevent_initial_call=True,
    )
    def render_watch_chart(
            _render_trigger,
            _load_request,
            watch_chart_state,
            _paper_trade_trigger,
            strategy_store,
            watch_timeframe,
            _ui_n,
            active_tab,
            symbol,
            price_source,
            replay_date,
    ):
        if active_tab != "watch":
            return no_update

        symbol = (symbol or DEFAULT_SYMBOL).upper().strip()
        trigger_id = ctx.triggered_id

        # Replay already drives Watch chart updates through replay-render-trigger.
        # Do not let the general UI interval cause duplicate chart redraws while
        # Watch is using replay data.
        if trigger_id == "ui-interval":
            selected_source = str(price_source or "replay").lower().strip()
            if selected_source != "live":
                return no_update

        # Paper trades should update the paper state immediately, but they
        # should not force an expensive chart redraw while replay is actively
        # playing. When paused, allow the redraw so markers appear immediately.
        if trigger_id == "paper-trade-trigger":
            selected_source = str(price_source or "replay").lower().strip()
            if selected_source != "live":
                try:
                    if bool(replay_service.info().get("playing")):
                        return no_update
                except Exception:
                    pass

        try:
            price_source = str(price_source or "replay").lower().strip()
            display_timeframe = str(watch_timeframe or "1 min")

            use_live_watch_data = (
                    price_source == "live"
                    and _is_today_or_latest_replay_date(replay_date)
            )
            try:
                replay_info_for_render = replay_service.info()
                replay_idx_for_render = max(
                    1,
                    int(replay_info_for_render.get("current_index", 1) or 1),
                )
                replay_max_idx_for_render = max(
                    1,
                    int(replay_info_for_render.get("max_index", 1) or 1),
                )
                is_replay_finished_for_render = (
                    not use_live_watch_data
                    and replay_max_idx_for_render > 1
                    and replay_idx_for_render >= replay_max_idx_for_render
                )
                is_replay_playing_for_render = (
                        not use_live_watch_data
                        and bool(replay_info_for_render.get("playing"))
                        and not is_replay_finished_for_render
                )
            except Exception:
                is_replay_playing_for_render = False
                is_replay_finished_for_render = False
                replay_idx_for_render = 1
                replay_max_idx_for_render = 1


            watch_view = bar_view_service.build_watch_view(
                rt=rt,
                replay_service=replay_service,
                symbol=symbol,
                display_timeframe=display_timeframe,
                use_live_watch_data=use_live_watch_data,
            )

            visible = watch_view.visible_bars
            chart_bars = watch_view.chart_bars
            current_price = watch_view.current_price

            source_label = watch_view.source
            if source_label not in {"live", "replay"}:
                source_label = "live" if use_live_watch_data else "replay"

            if watch_view.is_empty:
                message = watch_view.error or f"Loading {watch_view.chart_label} data..."
                fig = watch_chart_renderer.empty_figure(
                    f"{symbol} | {display_timeframe} | {message}"
                )
                fig.update_layout(uirevision=f"watch-{symbol}-{source_label}-empty")
                return fig

            fig = watch_chart_renderer.base_candles(
                chart_bars=chart_bars,
                symbol=symbol,
                display_timeframe=display_timeframe,
                current_price=current_price,
            )

            # Paper trade markers only belong on Watch.
            # During active replay playback, do not query fills and redraw markers on
            # every frame. Paper state still updates immediately; visual markers refresh
            # when replay is paused, stepped, reset, or otherwise redrawn.
            if paper_trading_service is not None and not is_replay_playing_for_render:
                try:
                    fills_df = paper_trading_service.fills_df()

                    if (
                            fills_df is not None
                            and not fills_df.empty
                            and "symbol" in fills_df.columns
                    ):
                        fills_df = fills_df[
                            fills_df["symbol"].astype(str).str.upper() == symbol.upper()
                            ]

                    fig = _add_trade_markers_to_fig(fig, chart_bars, fills_df)

                except Exception as exc:
                    print(f"[WATCH TRADE MARKER ERROR] {exc}", flush=True)

            # Strategy Lab overlays.
            try:
                strategy_store = strategy_store or {}
                script_text = str(strategy_store.get("script") or "").strip()
                strategy_enabled = bool(strategy_store.get("enabled"))

                if strategy_enabled and script_text:
                    try:
                        is_replay_playing = is_replay_playing_for_render
                    except Exception:
                        is_replay_playing = False

                    strategy_source_raw = watch_view.full_bars.copy()

                    if strategy_source_raw is None or strategy_source_raw.empty:
                        strategy_source_raw = visible.copy()

                    strategy_source_bars = bar_view_service.resample_bars(
                        strategy_source_raw,
                        display_timeframe,
                    )

                    if strategy_source_bars is not None and not strategy_source_bars.empty:
                        snapshot = strategy_overlay_service.get_or_run(
                            script=script_text,
                            bars=strategy_source_bars,
                            symbol=symbol,
                            timeframe=display_timeframe,
                            source_label=source_label,
                        )

                        if snapshot is not None:
                            strategy_result = snapshot.result

                            warning_key = (
                                symbol,
                                source_label,
                                display_timeframe,
                                len(strategy_source_bars),
                                tuple(strategy_result.errors or []),
                            )

                            if (
                                    strategy_result.errors
                                    and getattr(
                                render_watch_chart,
                                "_last_strategy_warning_key",
                                None,
                            )
                                    != warning_key
                            ):
                                print(
                                    "[STRATEGY SCRIPT WARNINGS] "
                                    + " | ".join(strategy_result.errors[:12]),
                                    flush=True,
                                )
                                render_watch_chart._last_strategy_warning_key = warning_key

                            fig = strategy_overlay_renderer.add_to_figure(
                                fig=fig,
                                engine=strategy_overlay_service.engine,
                                chart_bars=chart_bars,
                                strategy_result=strategy_result,
                                is_replay_playing=is_replay_playing,
                                context="WATCH",
                            )


            except Exception as strategy_exc:
                print(f"[STRATEGY OVERLAY ERROR] {strategy_exc}", flush=True)

            fig = chart_viewport_service.apply_chart_view(
                fig,
                chart_bars,
                watch_chart_state,
                default_range="1D",
            )

            state = watch_chart_state or {}
            range_key = chart_viewport_service.safe_range_key(
                state.get("range_key"),
                "1D",
            )
            mode = state.get("mode", "live")

            strategy_key = ""
            try:
                strategy_key = str((strategy_store or {}).get("nonce", ""))
            except Exception:
                strategy_key = ""

            idx = replay_idx_for_render or watch_view.current_index

            paper_key = ""
            if not is_replay_playing_for_render:
                try:
                    paper_key = str(_paper_trade_trigger or "")
                except Exception:
                    paper_key = ""

            fig.update_layout(
                uirevision=f"watch-{symbol}-{source_label}-{mode}-{range_key}",
                datarevision=(
                    f"watch-{symbol}-{source_label}-{mode}-{range_key}-"
                    f"{idx}-{strategy_key}-{paper_key}-{int(is_replay_playing_for_render)}"
                ),
                dragmode="pan",
            )

            return fig

        except Exception as exc:
            print(f"[WATCH CHART RENDER ERROR] {exc}", flush=True)
            fig = watch_chart_renderer.empty_figure(f"Replay loading... {exc}")
            fig.update_layout(uirevision=f"watch-{symbol or DEFAULT_SYMBOL}-error")
            return fig

    # ------------------------------------------------------------
    # Watch replay slider sync
    # ------------------------------------------------------------
    @app.callback(
        Output("replay-slider", "max", allow_duplicate=True),
        Output("replay-slider", "value", allow_duplicate=True),
        Input("replay-render-trigger", "data"),
        Input("watch-load-request", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def sync_watch_replay_slider(_render_trigger, _load_request, active_tab):
        if active_tab != "watch":
            return no_update, no_update

        try:
            info = replay_service.info()
            max_idx = max(1, int(info.get("max_index", 1) or 1))
            idx = max(1, int(info.get("current_index", 1) or 1))
            return max_idx, idx

        except Exception as exc:
            print(f"[WATCH SLIDER SYNC ERROR] {exc}", flush=True)
            return no_update, no_update

    # ------------------------------------------------------------
    # Watch metrics/stats render
    # ------------------------------------------------------------
    @app.callback(
        Output("watch-metrics-strip", "children"),
        Output("watch-stats-grid", "children"),
        Input("replay-render-trigger", "data"),
        Input("watch-load-request", "data"),
        Input("watch-timeframe-dropdown", "value"),
        Input("ui-interval", "n_intervals"),
        State("main-tabs", "value"),
        State("watch-symbol-dropdown", "value"),
        State("paper-price-source", "value"),
        State("replay-date", "date"),
        prevent_initial_call=True,
    )
    def render_watch_metrics_and_stats(
            _render_trigger,
            _load_request,
            watch_timeframe,
            _ui_n,
            active_tab,
            symbol,
            price_source,
            replay_date,
    ):
        if active_tab != "watch":
            return no_update, no_update

        symbol = (symbol or DEFAULT_SYMBOL).upper().strip()

        try:
            price_source = str(price_source or "replay").lower().strip()
            display_timeframe = str(watch_timeframe or "1 min")

            use_live_watch_data = (
                    price_source == "live"
                    and _is_today_or_latest_replay_date(replay_date)
            )

            watch_view = bar_view_service.build_watch_view(
                rt=rt,
                replay_service=replay_service,
                symbol=symbol,
                display_timeframe=display_timeframe,
                use_live_watch_data=use_live_watch_data,
            )

            if watch_view.is_empty:
                return [], []

            visible = watch_view.visible_bars
            chart_bars = watch_view.chart_bars
            current_price = watch_view.current_price
            updated_at = watch_view.updated_at or datetime.now()

            company = rt.get_company_name(symbol)
            open_val = (
                float(visible.iloc[0]["open"])
                if visible is not None and not visible.empty
                else None
            )

            metrics = _build_metrics_strip(
                symbol,
                company,
                current_price,
                open_val,
                updated_at,
            )

            stats = _build_stats_grid_from_bars(chart_bars)

            return metrics, stats

        except Exception as exc:
            print(f"[WATCH METRICS RENDER ERROR] {exc}", flush=True)
            return [], []

    @app.callback(
        Output("strategy-script-store", "data"),
        Output("strategy-script-input", "value"),
        Output("strategy-status", "children"),
        Input("strategy-run", "n_clicks"),
        Input("strategy-clear", "n_clicks"),
        State("strategy-script-input", "value"),
        State("strategy-script-store", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def update_strategy_script_store(
            run_clicks,
            clear_clicks,
            script_text,
            current_store,
            active_tab,
    ):
        if active_tab != "watch":
            return no_update, no_update, no_update

        trigger = ctx.triggered_id
        current_store = dict(current_store or {})
        nonce = int(current_store.get("nonce", 0)) + 1

        if trigger == "strategy-clear":
            return (
                {
                    "script": "",
                    "enabled": False,
                    "nonce": nonce,
                },
                "",
                "Strategy cleared.",
            )

        if trigger == "strategy-run":
            script_text = str(script_text or "").strip()

            if not script_text:
                return (
                    {
                        "script": "",
                        "enabled": False,
                        "nonce": nonce,
                    },
                    script_text,
                    "No strategy script entered.",
                )

            return (
                {
                    "script": script_text,
                    "enabled": True,
                    "nonce": nonce,
                },
                script_text,
                "Strategy script loaded. Indicators will draw on the Watch chart.",
            )

        return no_update, no_update, no_update



    # ------------------------------------------------------------
    # Paper trading
    # ------------------------------------------------------------
    def _paper_current_price_and_time(
            symbol: str,
            source: str = "replay",
            replay_date=None,
    ):
        symbol = (symbol or DEFAULT_SYMBOL).upper().strip()
        source = str(source or "replay").lower().strip()

        if source == "replay":
            try:
                bar = replay_service.current_bar()
                if bar is not None:
                    return float(bar["close"]), bar.get("time", datetime.now()), "Replay Cursor"
            except Exception:
                pass

            return None, datetime.now(), "Replay Cursor"

        if source == "live":
            if not _is_today_or_latest_replay_date(replay_date):
                return (
                    None,
                    datetime.now(),
                    "Live Market unavailable for historical dates",
                )

            try:
                rt.request_symbol(symbol)
            except Exception:
                pass

            snap = rt.get_snapshot(symbol, "1 min")

            if snap.last is None:
                return None, snap.updated_at or datetime.now(), "Live Market"

            return float(snap.last), snap.updated_at or datetime.now(), "Live Market"

        return None, datetime.now(), source

    @app.callback(
        Output("paper-short-buy", "className"),
        Output("paper-short-sell", "className"),
        Input("paper-position-mode", "value"),
        State("main-tabs", "value"),
        prevent_initial_call=False,
    )
    def toggle_short_trade_buttons(position_mode, active_tab):
        if active_tab != "watch":
            return no_update, no_update

        allow_short = str(position_mode or "long_only") == "allow_shorts"

        if allow_short:
            return (
                "paper-btn paper-short-btn",
                "paper-btn paper-short-btn",
            )

        return (
            "paper-btn paper-short-btn hidden",
            "paper-btn paper-short-btn hidden",
        )

    @app.callback(
        Output("paper-trade-status", "children"),
        Output("paper-trade-trigger", "data"),
        Input("paper-buy", "n_clicks"),
        Input("paper-sell", "n_clicks"),
        Input("paper-short-buy", "n_clicks"),
        Input("paper-short-sell", "n_clicks"),
        Input("paper-reset", "n_clicks"),
        State("paper-order-qty", "value"),
        State("watch-symbol-dropdown", "value"),
        State("paper-price-source", "value"),
        State("paper-position-mode", "value"),
        State("replay-date", "date"),
        State("paper-trade-trigger", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def handle_manual_paper_trade(
            buy_clicks,
            sell_clicks,
            short_buy_clicks,
            short_sell_clicks,
            reset_clicks,
            quantity,
            symbol,
            price_source,
            position_mode,
            replay_date,
            paper_trigger,
            active_tab,
    ):
        if active_tab != "watch":
            return no_update, no_update

        if paper_trading_service is None:
            return "Paper trading service is not enabled.", no_update

        trigger = ctx.triggered_id
        paper_trigger = int(paper_trigger or 0)

        try:
            if trigger == "paper-reset":
                paper_trading_service.reset()

                try:
                    if paper_state_cache is not None:
                        paper_state_cache.clear()
                        paper_state_cache.save_from_service(paper_trading_service)
                except Exception as cache_exc:
                    print(f"[PAPER CACHE RESET ERROR] {cache_exc}", flush=True)

                return "Paper account reset to starting cash.", paper_trigger + 1

            symbol = (symbol or DEFAULT_SYMBOL).upper().strip()

            try:
                quantity = float(quantity or 0)
            except Exception:
                return "Quantity must be numeric.", no_update

            if quantity <= 0:
                return "Quantity must be greater than zero.", no_update

            allow_short = str(position_mode or "long_only") == "allow_shorts"

            last_price, timestamp, source_label = _paper_current_price_and_time(
                symbol,
                source=price_source,
                replay_date=replay_date,
            )

            if last_price is None:
                return f"No price available from {source_label} for {symbol}.", no_update

            if trigger == "paper-buy":
                intent = TradeIntent(
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    order_type="MARKET",
                    reason="Manual paper buy",
                    source=f"manual:{source_label}",
                )

            elif trigger == "paper-sell":
                intent = TradeIntent(
                    symbol=symbol,
                    side="SELL",
                    quantity=quantity,
                    order_type="MARKET",
                    reason="Manual paper sell",
                    source=f"manual:{source_label}",
                )

            elif trigger == "paper-short-sell":
                if not allow_short:
                    return "Short selling is disabled. Select Allow Shorts first.", no_update

                intent = TradeIntent(
                    symbol=symbol,
                    side="SELL",
                    quantity=quantity,
                    order_type="MARKET",
                    reason="Manual short sell",
                    source=f"manual_short:{source_label}",
                )

            elif trigger == "paper-short-buy":
                if not allow_short:
                    return "Short buying/covering is disabled. Select Allow Shorts first.", no_update

                intent = TradeIntent(
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    order_type="MARKET",
                    reason="Manual short cover",
                    source=f"manual_short_cover:{source_label}",
                )

            else:
                return no_update, no_update

            print(
                f"[PAPER TRADE DEBUG] symbol={symbol} side={intent.side} "
                f"qty={quantity} price_source={price_source} "
                f"position_mode={position_mode} allow_short={allow_short}",
                flush=True,
            )

            decision, order = paper_trading_service.submit_intent(
                intent=intent,
                last_price=last_price,
                timestamp=timestamp,
                mode="simulated",
                allow_short=allow_short,
            )

            try:
                if paper_state_cache is not None:
                    paper_state_cache.save_from_service(
                        paper_trading_service,
                        prices={symbol: float(last_price)},
                    )
            except Exception as cache_exc:
                print(f"[PAPER CACHE SAVE ERROR] {cache_exc}", flush=True)


            if not decision.approved:
                return f"Risk rejected: {decision.message}", paper_trigger + 1

            if order is None:
                return "Order was approved but no order object was returned.", paper_trigger + 1

            fill_text = (
                f"{order.side} {order.quantity:g} {order.symbol} "
                f"@ {order.fill_price:,.2f}"
                if order.fill_price is not None
                else f"{order.side} {order.quantity:g} {order.symbol}"
            )

            mode_label = "Shorts allowed" if allow_short else "Long only"

            return (
                f"Paper order {order.status}: {fill_text} via {source_label} · {mode_label}",
                paper_trigger + 1,
            )

        except Exception as exc:
            print(f"[PAPER TRADE ERROR] {exc}", flush=True)
            return f"Paper trade error: {exc}", paper_trigger + 1

    def _paper_df_view(df, empty_message: str, max_rows: int = 8):
        if df is None or df.empty:
            return html.Div(empty_message, className="paper-empty")

        view = df.tail(max_rows).copy()

        datetime_cols = {
            "submitted_at",
            "filled_at",
            "timestamp",
        }

        for col in view.columns:
            col_lower = str(col).lower()

            if col_lower in datetime_cols:
                try:
                    view[col] = pd.to_datetime(
                        view[col],
                        errors="coerce",
                        format="mixed",
                    ).dt.strftime("%Y-%m-%d %H:%M:%S")

                    view[col] = view[col].fillna("")
                except Exception:
                    pass

        return html.Pre(
            view.to_string(index=False),
            className="paper-table",
        )

    @app.callback(
        Output("paper-trade-status", "children", allow_duplicate=True),
        Input("paper-price-source", "value"),
        Input("replay-date", "date"),
        State("main-tabs", "value"),
        prevent_initial_call=True,
    )
    def warn_live_source_for_historical_date(price_source, replay_date, active_tab):
        if active_tab != "watch":
            return no_update

        if price_source == "live" and not _is_today_or_latest_replay_date(replay_date):
            return "Live Market paper trading is only available for today's date or latest mode."

        if price_source == "live":
            return "Live Market paper trading enabled for today's/current data."

        return "Replay Cursor paper trading enabled."


    @app.callback(
        Output("paper-summary-panel", "children"),
        Output("paper-positions-panel", "children"),
        Output("paper-orders-panel", "children"),
        Output("paper-fills-panel", "children"),
        Input("paper-trade-trigger", "data"),
        Input("replay-render-trigger", "data"),
        Input("ui-interval", "n_intervals"),
        State("watch-symbol-dropdown", "value"),
        State("paper-price-source", "value"),
        State("replay-date", "date"),
        State("main-tabs", "value"),
        prevent_initial_call=False,
    )
    def render_paper_trading_panels(
            _paper_trigger,
            _replay_trigger,
            _ui_n,
            symbol,
            price_source,
            replay_date,
            active_tab,
    ):

        if active_tab != "watch":
            return no_update, no_update, no_update, no_update

        if paper_trading_service is None:
            disabled = html.Div("Paper trading service is disabled.", className="paper-empty")
            return disabled, disabled, disabled, disabled

        symbol = (symbol or DEFAULT_SYMBOL).upper().strip()

        prices = {}
        try:
            price, _timestamp, _source_label = _paper_current_price_and_time(
                symbol,
                source=price_source,
                replay_date=replay_date,
            )

            if price is not None:
                prices[symbol] = float(price)
        except Exception:
            pass

        summary = paper_trading_service.summary(prices=prices)

        summary_cards = html.Div(
            className="paper-summary-cards",
            children=[
                html.Div(
                    className="paper-summary-card",
                    children=[
                        html.Div("Cash", className="paper-summary-label"),
                        html.Div(f"${summary.get('cash', 0):,.2f}", className="paper-summary-value"),
                    ],
                ),
                html.Div(
                    className="paper-summary-card",
                    children=[
                        html.Div("Equity", className="paper-summary-label"),
                        html.Div(f"${summary.get('equity', 0):,.2f}", className="paper-summary-value"),
                    ],
                ),
                html.Div(
                    className="paper-summary-card",
                    children=[
                        html.Div("Open Positions", className="paper-summary-label"),
                        html.Div(f"{summary.get('open_positions', 0)}", className="paper-summary-value"),
                    ],
                ),
                html.Div(
                    className="paper-summary-card",
                    children=[
                        html.Div("Orders / Fills", className="paper-summary-label"),
                        html.Div(
                            f"{summary.get('orders', 0)} / {summary.get('fills', 0)}",
                            className="paper-summary-value",
                        ),
                    ],
                ),
            ],
        )

        positions = _paper_df_view(
            paper_trading_service.positions_df(),
            "No open positions.",
        )

        orders = _paper_df_view(
            paper_trading_service.orders_df(),
            "No orders yet.",
        )

        fills = _paper_df_view(
            paper_trading_service.fills_df(),
            "No fills yet.",
        )


        return summary_cards, positions, orders, fills

    def _add_trade_markers_to_fig(fig, bars, fills_df):
        """
        Add paper-trade fill markers to a candlestick chart.

        v1G fix:
        - Supports timestamp, filled_at, submitted_at, or time columns.
        - Does not require order_id.
        - Works on resampled chart bars by mapping each fill to the current
          chart candle with merge_asof instead of exact minute equality.
        """
        if bars is None or bars.empty:
            return fig

        if fills_df is None or fills_df.empty:
            return fig

        fills = fills_df.copy()

        if "side" not in fills.columns or "price" not in fills.columns:
            return fig

        if "quantity" not in fills.columns:
            fills["quantity"] = 1

        time_col = None
        for candidate in ("timestamp", "filled_at", "submitted_at", "time"):
            if candidate in fills.columns:
                time_col = candidate
                break

        if time_col is None:
            return fig

        df_bars = bars.copy()

        if "time" not in df_bars.columns:
            return fig

        df_bars["bar_time"] = pd.to_datetime(
            df_bars["time"],
            errors="coerce",
            format="mixed",
        )

        for col in ("high", "low", "close"):
            if col not in df_bars.columns:
                return fig
            df_bars[col] = pd.to_numeric(df_bars[col], errors="coerce")

        df_bars = (
            df_bars
            .dropna(subset=["bar_time", "high", "low", "close"])
            .sort_values("bar_time")
            .reset_index(drop=True)
        )

        if df_bars.empty:
            return fig

        fills["_fill_time"] = pd.to_datetime(
            fills[time_col],
            errors="coerce",
            format="mixed",
        )

        fills["price"] = pd.to_numeric(fills["price"], errors="coerce")
        fills["quantity"] = pd.to_numeric(fills["quantity"], errors="coerce").fillna(0)

        fills = (
            fills
            .dropna(subset=["_fill_time", "price"])
            .sort_values("_fill_time")
            .reset_index(drop=True)
        )

        if fills.empty:
            return fig

        try:
            bar_steps = df_bars["bar_time"].diff().dropna()
            if not bar_steps.empty:
                median_step = bar_steps.median()
                tolerance = max(median_step * 2, pd.Timedelta(minutes=2))
            else:
                tolerance = pd.Timedelta(minutes=2)
        except Exception:
            tolerance = pd.Timedelta(minutes=2)

        try:
            merged = pd.merge_asof(
                fills,
                df_bars[["bar_time", "high", "low", "close"]],
                left_on="_fill_time",
                right_on="bar_time",
                direction="backward",
                tolerance=tolerance,
            )
        except Exception as marker_merge_exc:
            print(f"[WATCH TRADE MARKER MERGE ERROR] {marker_merge_exc}", flush=True)
            return fig

        merged = merged.dropna(subset=["bar_time", "high", "low", "close"]).copy()

        if merged.empty:
            return fig

        if "order_id" not in merged.columns:
            merged["order_id"] = range(1, len(merged) + 1)

        grouped_rows = []

        for (bar_time, side), group in merged.groupby(["bar_time", "side"]):
            side = str(side).upper().strip()

            if side not in {"BUY", "SELL"}:
                continue

            total_qty = float(group["quantity"].sum())
            if total_qty <= 0:
                continue

            try:
                avg_price = float((group["price"] * group["quantity"]).sum() / total_qty)
            except Exception:
                avg_price = float(group["price"].iloc[-1])

            order_ids = ", ".join(str(x) for x in group["order_id"].tolist())
            count = len(group)

            high = float(group["high"].iloc[0])
            low = float(group["low"].iloc[0])
            close = float(group["close"].iloc[0])

            candle_range = max(high - low, abs(close) * 0.002, 0.01)
            offset = candle_range * 0.45

            if side == "BUY":
                y = high + offset
                marker_symbol = "triangle-up"
                label = f"BUY x{count}" if count > 1 else "BUY"
                text_position = "top center"
            else:
                y = low - offset
                marker_symbol = "triangle-down"
                label = f"SELL x{count}" if count > 1 else "SELL"
                text_position = "bottom center"

            realized = 0.0
            if "realized_pnl" in group.columns:
                realized = float(
                    pd.to_numeric(group["realized_pnl"], errors="coerce")
                    .fillna(0)
                    .sum()
                )

            sources = []
            if "source" in group.columns:
                sources = [
                    str(s)
                    for s in group["source"].dropna().tolist()
                    if str(s).strip()
                ]

            reasons = []
            if "reason" in group.columns:
                reasons = [
                    str(r)
                    for r in group["reason"].dropna().tolist()
                    if str(r).strip()
                ]

            hover = (
                f"<b>{label}</b><br>"
                f"Fill time: {group['_fill_time'].iloc[-1]}<br>"
                f"Chart candle: {bar_time}<br>"
                f"Orders: {order_ids}<br>"
                f"Quantity: {total_qty:g}<br>"
                f"Avg Fill: ${avg_price:,.2f}<br>"
                f"Realized PnL: ${realized:,.2f}<br>"
                f"Source: {', '.join(sorted(set(sources))) if sources else 'manual'}<br>"
                f"Reason: {' | '.join(reasons) if reasons else '--'}"
            )

            grouped_rows.append(
                {
                    "time": bar_time,
                    "side": side,
                    "y": y,
                    "symbol": marker_symbol,
                    "label": label,
                    "hover": hover,
                    "textposition": text_position,
                }
            )

        if not grouped_rows:
            return fig

        marker_df = pd.DataFrame(grouped_rows)

        buys = marker_df[marker_df["side"] == "BUY"]
        sells = marker_df[marker_df["side"] == "SELL"]

        if not buys.empty:
            fig.add_trace(
                go.Scatter(
                    x=buys["time"],
                    y=buys["y"],
                    mode="markers+text",
                    marker=dict(
                        symbol="triangle-up",
                        size=16,
                        color="#22c55e",
                        line=dict(width=1, color="#ffffff"),
                    ),
                    text=buys["label"],
                    textposition="top center",
                    hovertext=buys["hover"],
                    hoverinfo="text",
                    name="Paper Buys",
                )
            )

        if not sells.empty:
            fig.add_trace(
                go.Scatter(
                    x=sells["time"],
                    y=sells["y"],
                    mode="markers+text",
                    marker=dict(
                        symbol="triangle-down",
                        size=16,
                        color="#ef4444",
                        line=dict(width=1, color="#ffffff"),
                    ),
                    text=sells["label"],
                    textposition="bottom center",
                    hovertext=sells["hover"],
                    hoverinfo="text",
                    name="Paper Sells",
                )
            )

        return fig

    # ------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------
    @app.callback(
        Output("quotes-status", "children"),
        Output("quotes-panel", "children"),
        Input("ui-interval", "n_intervals"),
        Input("quotes-symbol-dropdown", "value"),
        State("main-tabs", "value"),
        prevent_initial_call=False,
    )
    def render_quotes_tab(_n, symbol, active_tab):
        if active_tab != "quotes":
            return no_update, no_update

        try:
            symbol = symbol or DEFAULT_SYMBOL
            snap = rt.get_snapshot(symbol, "1 min")
            company = rt.get_company_name(symbol)

            bid = f"{snap.bid:.2f}" if snap.bid is not None else "--"
            ask = f"{snap.ask:.2f}" if snap.ask is not None else "--"
            last = f"{snap.last:.2f}" if snap.last is not None else "--"
            size = f"{snap.last_size:.0f}" if snap.last_size is not None else "--"
            updated = snap.updated_at.strftime("%H:%M:%S") if snap.updated_at else "--:--:--"

            quote_text = [
                html.Div(f"Company: {company}"),
                html.Div(f"Symbol: {symbol}"),
                html.Div(f"Last: {last}"),
                html.Div(f"Bid: {bid}"),
                html.Div(f"Ask: {ask}"),
                html.Div(f"Last Size: {size}"),
                html.Div(f"Updated: {updated}"),
            ]

            return f"Quotes loaded for {symbol}", quote_text

        except Exception as exc:
            return f"Quotes error: {exc}", f"Unable to load quotes for {symbol or DEFAULT_SYMBOL}"

    # ------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------
    @app.callback(
        Output("charts-chart-state", "data"),
        Input("charts-live-mode", "n_clicks"),
        Input("charts-reset-view", "n_clicks"),
        Input("charts-range-1d", "n_clicks"),
        Input("charts-range-1w", "n_clicks"),
        Input("charts-range-1m", "n_clicks"),
        Input("charts-range-3m", "n_clicks"),
        Input("charts-range-1y", "n_clicks"),
        Input("charts-range-5y", "n_clicks"),
        Input("charts-range-max", "n_clicks"),
        Input("charts-main-graph", "relayoutData"),
        State("charts-chart-state", "data"),
        prevent_initial_call=True,
    )
    def update_charts_chart_state(*args):
        current_state = dict(args[-1] or _default_chart_state())
        relayout_data = args[-2]
        trigger_id = ctx.triggered_id

        if trigger_id in {"charts-live-mode", "charts-reset-view"}:
            return _default_chart_state(current_state.get("range_key", "1D"))

        if isinstance(trigger_id, str) and trigger_id.startswith("charts-range-"):
            range_key = _range_key_from_button(trigger_id, "charts-range-", "1D")
            return _default_chart_state(range_key)

        if trigger_id == "charts-main-graph":
            parsed = _clean_relayout_range(relayout_data)
            if parsed is no_update:
                return no_update

            new_state = dict(current_state)
            new_state.update(parsed)
            new_state["range_key"] = current_state.get("range_key", "1D")
            return new_state

        return no_update

    @app.callback(
        Output("charts-status", "children"),
        Output("charts-main-graph", "figure"),
        Output("chart-metrics-strip", "children"),
        Output("charts-stats-grid", "children"),
        Input("ui-interval", "n_intervals"),
        Input("charts-symbol-dropdown", "value"),
        Input("charts-timeframe-dropdown", "value"),
        Input("charts-chart-state", "data"),
        State("main-tabs", "value"),
        prevent_initial_call=False,
    )
    def render_charts_tab(_n, symbol, timeframe, charts_chart_state, active_tab):
        if active_tab != "charts":
            return no_update, no_update, no_update, no_update

        try:
            symbol = (symbol or DEFAULT_SYMBOL).upper().strip()
            timeframe = timeframe or DEFAULT_TIMEFRAME
            company_name = rt.get_company_name(symbol)

            try:
                rt.request_symbol(symbol)
            except Exception as req_exc:
                print(f"[CHARTS REQUEST WARNING] {symbol}: {req_exc}", flush=True)

            try:
                snap = rt.get_snapshot(symbol, timeframe)
            except Exception as snapshot_exc:
                msg = str(snapshot_exc)
                if "No loaded state" in msg:
                    try:
                        rt.request_symbol(symbol)
                    except Exception:
                        pass
                    fig = _empty_figure(f"{symbol} | Loading live candles...")
                    status_text = f"LIVE · {company_name} ({symbol}) · Loading live data..."
                    metrics = [
                        html.Div(f"{symbol} / {company_name}", className="metric-price"),
                        html.Div("Waiting for live candles...", className="metric-muted"),
                    ]
                    stats = [html.Div(className="stat-card", children=[html.Div("Waiting for bars...", className="stat-label")])]
                    return status_text, fig, metrics, stats
                raise

            bars = snap.bars.copy() if getattr(snap, "bars", None) is not None else pd.DataFrame()
            if not bars.empty:
                bars["time"] = pd.to_datetime(bars["time"], errors="coerce", format="mixed")
                bars = bars.dropna(subset=["time", "open", "high", "low", "close"]).copy()

            if bars.empty:
                fig = _empty_figure(f"{symbol} | Waiting for live candles...")
                status_text = f"LIVE · {company_name} ({symbol}) · Waiting for candles"
                return status_text, fig, [], []

            latest_time = str(bars.iloc[-1]["time"])
            latest_open = float(bars.iloc[-1]["open"])
            latest_high = float(bars.iloc[-1]["high"])
            latest_low = float(bars.iloc[-1]["low"])
            latest_close = float(bars.iloc[-1]["close"])
            current_price = float(snap.last) if getattr(snap, "last", None) is not None else latest_close

            fig = create_candlestick_figure(bars, symbol, timeframe, current_price=current_price)
            fig = _apply_chart_view(fig, bars, charts_chart_state, default_range="1D")

            try:
                if fig.data:
                    for trace in fig.data:
                        if hasattr(trace, "x") and trace.x is not None:
                            trace.x = list(trace.x)
                        if hasattr(trace, "open") and trace.open is not None:
                            trace.open = [float(x) for x in trace.open]
                        if hasattr(trace, "high") and trace.high is not None:
                            trace.high = [float(x) for x in trace.high]
                        if hasattr(trace, "low") and trace.low is not None:
                            trace.low = [float(x) for x in trace.low]
                        if hasattr(trace, "close") and trace.close is not None:
                            trace.close = [float(x) for x in trace.close]
            except Exception as trace_exc:
                print(f"[CHARTS TRACE NORMALIZE WARNING] {trace_exc}", flush=True)

            state = charts_chart_state or {}
            range_key = _safe_range_key(state.get("range_key"), "1D")
            mode = state.get("mode", "live")
            redraw_key = f"{symbol}-{timeframe}-{mode}-{range_key}-{latest_time}-{latest_open}-{latest_high}-{latest_low}-{latest_close}-{current_price}-{_n}"
            fig.update_layout(uirevision=None, datarevision=redraw_key, dragmode="pan", title={"text": f"{symbol} · {timeframe} · Last {current_price:,.2f} · tick {_n}", "x": 0.02, "xanchor": "left"})

            updated = snap.updated_at.strftime("%H:%M:%S") if getattr(snap, "updated_at", None) else "--:--:--"
            status_text = f"LIVE · {company_name} ({symbol}) · Updated {updated} · Last {current_price:,.2f}"

            open_val = float(bars.iloc[0]["open"])
            metrics = _build_metrics_strip(symbol, company_name, current_price, open_val, snap.updated_at)
            stats = _build_stats_grid_from_bars(bars)

            return status_text, fig, metrics, stats

        except Exception as exc:
            print(f"[CHARTS RENDER ERROR] {exc}", flush=True)
            fig = _empty_figure(f"Charts tab error: {exc}")
            return f"Charts error: {exc}", fig, [], []
