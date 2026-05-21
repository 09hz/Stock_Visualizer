from __future__ import annotations

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html, no_update

from RealTime import RealTimeIB, TIMEFRAME_MAP
from chart_utils import create_candlestick_figure


DEFAULT_SYMBOL = "MSFT"
DEFAULT_TIMEFRAME = "1 min"

rt = RealTimeIB(host="127.0.0.1", port=4001)
rt.start(DEFAULT_SYMBOL, DEFAULT_TIMEFRAME)

SYMBOL_OPTIONS = rt.get_symbol_options()

app = Dash(__name__)
app.title = "Stock Visualizer"

app.layout = html.Div(
    className="app-shell",
    children=[
        html.Div(
            className="sidebar",
            children=[
                html.Div("SV", className="logo"),
                html.Div("Dashboard", className="nav-item active"),
                html.Div("Watch", className="nav-item"),
                html.Div("Quotes", className="nav-item"),
                html.Div("Charts", className="nav-item"),
            ],
        ),
        html.Div(
            className="main-panel",
            children=[
                html.Div(
                    className="topbar",
                    children=[
                        html.Div(id="pair-title", className="pair-title"),
                        html.Div(id="quote-strip", className="quote-strip"),
                    ],
                ),
                html.Div(
                    className="controls-row",
                    children=[
                        html.Div(
                            className="control-box control-symbol",
                            children=[
                                html.Label("Symbol / Company"),
                                dcc.Dropdown(
                                    id="symbol-dropdown",
                                    options=SYMBOL_OPTIONS,
                                    value=DEFAULT_SYMBOL,
                                    placeholder="Search ticker or company...",
                                    searchable=True,
                                    clearable=False,
                                    style={"color": "black"},
                                ),
                            ],
                        ),
                        html.Div(
                            className="control-box control-timeframe",
                            children=[
                                html.Label("Timeframe"),
                                dcc.Dropdown(
                                    id="timeframe-dropdown",
                                    options=[
                                        {"label": key, "value": key}
                                        for key in TIMEFRAME_MAP.keys()
                                    ],
                                    value=DEFAULT_TIMEFRAME,
                                    clearable=False,
                                    style={"color": "black"},
                                ),
                            ],
                        ),
                    ],
                ),
                html.Div(id="load-status-text", className="status-text"),
                html.Div(
                    className="chart-card",
                    children=[
                        dcc.Graph(
                            id="live-chart",
                            className="chart-graph",
                            config={
                                "displaylogo": False,
                                "modeBarButtonsToRemove": [
                                    "lasso2d",
                                    "select2d",
                                    "autoScale2d",
                                ],
                            },
                        ),
                    ],
                ),
                dcc.Interval(id="ui-interval", interval=250, n_intervals=0),
                dcc.Store(id="zoom-state", data={}),
                dcc.Store(id="active-symbol", data=DEFAULT_SYMBOL),
                dcc.Store(id="load-status", data="Ready"),
            ],
        ),
    ],
)


@app.callback(
    Output("pair-title", "children"),
    Input("active-symbol", "data"),
)
def update_pair_title(symbol):
    symbol = symbol or DEFAULT_SYMBOL
    company = rt.get_company_name(symbol)
    return f"{symbol} / {company}"


@app.callback(
    Output("zoom-state", "data"),
    Input("live-chart", "relayoutData"),
    State("zoom-state", "data"),
    prevent_initial_call=True,
)
def capture_zoom(relayout_data, current_state):
    if relayout_data is None:
        return current_state
    return relayout_data


@app.callback(
    Output("active-symbol", "data"),
    Output("load-status", "data"),
    Input("symbol-dropdown", "value"),
    prevent_initial_call=True,
)
def auto_load_symbol(symbol):
    print(f"[SYMBOL CHANGE] symbol={symbol}", flush=True)

    if not symbol:
        return no_update, "No symbol selected"

    try:
        symbol = rt._sanitize_symbol(symbol)
        rt.request_symbol(symbol)
        return symbol, f"Requested {symbol}"
    except Exception as exc:
        return no_update, f"Error: {exc}"


@app.callback(
    Output("load-status-text", "children"),
    Input("load-status", "data"),
)
def show_load_status(status):
    return status


@app.callback(
    Output("quote-strip", "children"),
    Output("live-chart", "figure"),
    Input("ui-interval", "n_intervals"),
    Input("active-symbol", "data"),
    Input("timeframe-dropdown", "value"),
    State("zoom-state", "data"),
)
def render_live_chart(_n, active_symbol, timeframe, zoom_state):
    try:
        symbol = active_symbol or DEFAULT_SYMBOL
        timeframe = timeframe or DEFAULT_TIMEFRAME
        company_name = rt.get_company_name(symbol)

        snap = rt.get_snapshot(symbol, timeframe)
        fig = create_candlestick_figure(snap.bars, symbol, timeframe)

        bid = f"{snap.bid:.2f}" if snap.bid is not None else "--"
        ask = f"{snap.ask:.2f}" if snap.ask is not None else "--"
        last = f"{snap.last:.2f}" if snap.last is not None else "--"
        size = f"{snap.last_size:.0f}" if snap.last_size is not None else "--"
        updated = snap.updated_at.strftime("%H:%M:%S") if snap.updated_at else "--:--:--"

        quote_text = (
            f"{symbol} ({company_name}) | "
            f"Last: {last} | Bid: {bid} | Ask: {ask} | "
            f"Last Size: {size} | Ticks: {snap.tick_count} | Updated: {updated}"
        )

        if zoom_state:
            if "xaxis.range[0]" in zoom_state and "xaxis.range[1]" in zoom_state:
                fig.update_xaxes(
                    range=[zoom_state["xaxis.range[0]"], zoom_state["xaxis.range[1]"]],
                    row=1,
                    col=1,
                )
            if "yaxis.range[0]" in zoom_state and "yaxis.range[1]" in zoom_state:
                fig.update_yaxes(
                    range=[zoom_state["yaxis.range[0]"], zoom_state["yaxis.range[1]"]],
                    row=1,
                    col=1,
                )

        return quote_text, fig

    except Exception as exc:
        fig = go.Figure()
        fig.update_layout(
            title=f"Loading {active_symbol or DEFAULT_SYMBOL}...",
            template="plotly_dark",
            paper_bgcolor="#0d1b4f",
            plot_bgcolor="#0d1b4f",
            font={"color": "#e8f1ff"},
        )
        return f"Loading {active_symbol or DEFAULT_SYMBOL}...", fig


if __name__ == "__main__":
    try:
        app.run(debug=False)
    finally:
        rt.disconnect()