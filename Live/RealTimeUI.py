from __future__ import annotations

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html, no_update

from RealTime import RealTimeIB, TIMEFRAME_MAP
from chart_utils import create_candlestick_figure


DEFAULT_SYMBOL = "MSFT"
DEFAULT_TIMEFRAME = "1 min"

rt = RealTimeIB(host="127.0.0.1", port=4001)
rt.start(DEFAULT_SYMBOL, DEFAULT_TIMEFRAME)

SYMBOL_OPTIONS = [
    {"label": symbol, "value": symbol}
    for symbol in rt.get_symbol_options()
]

app = Dash(__name__)
app.title = "RealTime IBKR Viewer (ib_async)"

app.layout = html.Div(
    [
        html.Header(
            [
                html.H1("RealTime IBKR Viewer (ib_async)"),
                html.Div(
                    "Live market data dashboard with Plotly + Dash",
                    className="subtitle"
                ),
            ]
        ),

        html.Main(
            [
                html.Section(
                    [
                        html.H2("Controls"),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Label("Symbol"),
                                        dcc.Dropdown(
                                            id="symbol-dropdown",
                                            options=SYMBOL_OPTIONS,
                                            value=DEFAULT_SYMBOL,
                                            placeholder="Search NASDAQ ticker...",
                                            searchable=True,
                                            clearable=False,
                                            style={"width": "260px", "color": "black"},
                                        ),
                                    ],
                                    style={"display": "inline-block", "marginRight": "16px"}
                                ),
                                html.Div(
                                    [
                                        html.Label("Timeframe"),
                                        dcc.Dropdown(
                                            id="timeframe-dropdown",
                                            options=[
                                                {"label": key, "value": key}
                                                for key in TIMEFRAME_MAP.keys()
                                            ],
                                            value=DEFAULT_TIMEFRAME,
                                            clearable=False,
                                            style={"width": "220px", "display": "inline-block", "color": "black"},
                                        ),
                                    ],
                                    style={"display": "inline-block", "marginRight": "16px"}
                                ),
                                html.Div(
                                    [
                                        html.Label(" "),
                                        html.Button(
                                            "Load Symbol",
                                            id="load-symbol-btn",
                                            n_clicks=0,
                                            style={
                                                "height": "38px",
                                                "padding": "0 16px",
                                                "cursor": "pointer"
                                            }
                                        ),
                                    ],
                                    style={"display": "inline-block"}
                                ),
                            ]
                        ),
                        html.Div(
                            id="load-status-text",
                            style={"marginTop": "10px", "color": "#38bdf8"}
                        ),
                    ],
                    className="about",
                ),

                html.Section(
                    [
                        html.H2("Live Quote"),
                        html.Div(id="quote-strip"),
                    ],
                    className="about",
                ),

                html.Section(
                    [
                        html.H2("Live Chart"),
                        html.Div(
                            [
                                dcc.Graph(id="live-chart"),
                            ],
                            className="project-card",
                        ),
                    ],
                    className="projects",
                ),

                dcc.Interval(id="ui-interval", interval=250, n_intervals=0),
                dcc.Store(id="zoom-state", data={}),
                dcc.Store(id="active-symbol", data=DEFAULT_SYMBOL),
                dcc.Store(id="load-status", data="Ready"),
            ]
        ),
    ]
)


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
    Input("load-symbol-btn", "n_clicks"),
    State("symbol-dropdown", "value"),
    prevent_initial_call=True,
)
def load_new_symbol(n_clicks, symbol):
    print(f"[LOAD SYMBOL] n_clicks={n_clicks} symbol={symbol}", flush=True)

    if not n_clicks:
        return no_update, no_update

    if not symbol:
        return no_update, "No symbol selected"

    try:
        symbol = rt._sanitize_symbol(symbol)
        rt.request_symbol(symbol)
        print(f"[ACTIVE SYMBOL SET] {symbol}", flush=True)
        return symbol, f"Requested {symbol}"
    except Exception as exc:
        print(f"[LOAD ERROR] {exc}", flush=True)
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

        print(f"[RENDER] symbol={symbol} timeframe={timeframe}", flush=True)

        snap = rt.get_snapshot(symbol, timeframe)
        fig = create_candlestick_figure(snap.bars, symbol, timeframe)

        bid = f"{snap.bid:.2f}" if snap.bid is not None else "--"
        ask = f"{snap.ask:.2f}" if snap.ask is not None else "--"
        last = f"{snap.last:.2f}" if snap.last is not None else "--"
        size = f"{snap.last_size:.0f}" if snap.last_size is not None else "--"
        updated = snap.updated_at.strftime("%H:%M:%S") if snap.updated_at else "--:--:--"

        quote_text = (
            f"Symbol: {symbol} | Last: {last} | Bid: {bid} | Ask: {ask} | "
            f"Last Size: {size} | Ticks: {snap.tick_count} | Updated: {updated}"
        )

        if zoom_state:
            if "xaxis.range[0]" in zoom_state and "xaxis.range[1]" in zoom_state:
                fig.update_xaxes(
                    range=[zoom_state["xaxis.range[0]"], zoom_state["xaxis.range[1]"]],
                    row=1, col=1
                )
            if "yaxis.range[0]" in zoom_state and "yaxis.range[1]" in zoom_state:
                fig.update_yaxes(
                    range=[zoom_state["yaxis.range[0]"], zoom_state["yaxis.range[1]"]],
                    row=1, col=1
                )

        return quote_text, fig

    except Exception as exc:
        print(f"[RENDER ERROR] {exc}", flush=True)
        fig = go.Figure()
        fig.update_layout(
            title=f"Loading {active_symbol or DEFAULT_SYMBOL}...",
            template="plotly_dark"
        )
        return f"Loading {active_symbol or DEFAULT_SYMBOL}...", fig


if __name__ == "__main__":
    try:
        app.run(debug=False)
    finally:
        rt.disconnect()