from __future__ import annotations

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html

from RealTime import RealTimeIB
from chart_utils import create_candlestick_figure


DEFAULT_SYMBOL = "MSFT"
DEFAULT_TIMEFRAME = "1 min"

rt = RealTimeIB(host="127.0.0.1", port=4001)
rt.start(DEFAULT_SYMBOL, DEFAULT_TIMEFRAME)

app = Dash(__name__)
app.title = "RealTime IBKR Viewer (ib_async)"

app.layout = html.Div(
    [
        html.H2("RealTime IBKR Viewer (ib_async)"),
        html.Div(id="quote-strip", style={"marginBottom": "10px", "fontFamily": "monospace"}),
        dcc.Interval(id="ui-interval", interval=250, n_intervals=0),
        dcc.Store(id="zoom-state", data={}),
        dcc.Graph(id="live-chart"),
    ],
    style={"padding": "16px"},
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
    Output("quote-strip", "children"),
    Output("live-chart", "figure"),
    Input("ui-interval", "n_intervals"),
    State("zoom-state", "data"),
)
def render_live_chart(_n, zoom_state):
    try:
        snap = rt.get_snapshot(DEFAULT_SYMBOL, DEFAULT_TIMEFRAME)
        fig = create_candlestick_figure(snap.bars, DEFAULT_SYMBOL, DEFAULT_TIMEFRAME)

        bid = f"{snap.bid:.2f}" if snap.bid is not None else "--"
        ask = f"{snap.ask:.2f}" if snap.ask is not None else "--"
        last = f"{snap.last:.2f}" if snap.last is not None else "--"
        size = f"{snap.last_size:.0f}" if snap.last_size is not None else "--"
        updated = snap.updated_at.strftime("%H:%M:%S") if snap.updated_at else "--:--:--"

        quote_text = (
            f"Last: {last} | Bid: {bid} | Ask: {ask} | Last Size: {size} | "
            f"Ticks: {snap.tick_count} | Updated: {updated}"
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
        fig = go.Figure()
        fig.update_layout(title=f"Error: {exc}", template="plotly_dark")
        return f"Error: {exc}", fig


if __name__ == "__main__":
    try:
        app.run(debug=False)
    finally:
        rt.disconnect()