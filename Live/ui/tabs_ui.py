from dash import dcc, html
from datetime import date, timedelta

from rich import color

CHART_CONFIG = {
    "displaylogo": False,
    "scrollZoom": False,
    "doubleClick": "reset",
    "modeBarButtonsToRemove": [
        "lasso2d",
        "select2d",
        "autoScale2d",
    ],
}


def make_timeframe_options(timeframe_map):
    return [
        {
            "label": k,
            "value": k,
            "search": k,
        }
        for k in timeframe_map.keys()
    ]


def make_replay_speed_options():
    return [
        {"label": "0.25x", "value": 0.25, "search": "0.25x quarter slow"},
        {"label": "0.5x", "value": 0.5, "search": "0.5x half slow"},
        {"label": "1x", "value": 1, "search": "1x normal default"},
        {"label": "2x", "value": 2, "search": "2x double fast"},
        {"label": "5x", "value": 5, "search": "5x very fast"},
    ]



def make_disabled_weekend_days(years_back=10, years_forward=1):
    """
    Disable Saturdays and Sundays in Dash DatePickerSingle.
    Dash expects disabled_days as YYYY-MM-DD strings.
    """
    start = date.today() - timedelta(days=365 * years_back)
    end = date.today() + timedelta(days=365 * years_forward)

    disabled = []
    current = start

    while current <= end:
        if current.weekday() >= 5:
            disabled.append(current.isoformat())

        current += timedelta(days=1)

    return disabled


def make_chart_control_buttons(prefix: str):
    return [
        html.Button("Live", id=f"{prefix}-live-mode", n_clicks=0, className="range-btn active"),
        html.Button("1D", id=f"{prefix}-range-1d", n_clicks=0, className="range-btn"),
        html.Button("1W", id=f"{prefix}-range-1w", n_clicks=0, className="range-btn"),
        html.Button("1M", id=f"{prefix}-range-1m", n_clicks=0, className="range-btn"),
        html.Button("3M", id=f"{prefix}-range-3m", n_clicks=0, className="range-btn"),
        html.Button("1Y", id=f"{prefix}-range-1y", n_clicks=0, className="range-btn"),
        html.Button("5Y", id=f"{prefix}-range-5y", n_clicks=0, className="range-btn"),
        html.Button("Max", id=f"{prefix}-range-max", n_clicks=0, className="range-btn"),
        html.Button("Reset", id=f"{prefix}-reset-view", n_clicks=0, className="range-btn"),
    ]


def build_dashboard_tab(symbol_options, timeframe_map, default_symbol, default_timeframe):
    return html.Div(
        className="tab-panel dashboard-tab-panel",
        children=[
            html.Div(
                className="controls-row",
                children=[
                    html.Div(
                        className="control-box control-symbol",
                        children=[
                            html.Label("Instrument"),
                            dcc.Dropdown(
                                id="symbol-dropdown",
                                options=symbol_options,
                                value=default_symbol,
                                placeholder="Search ticker, symbol, or company...",
                                searchable=True,
                                clearable=False,
                                className="black-dropdown",
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box control-timeframe",
                        children=[
                            html.Label("Interval"),
                            dcc.Dropdown(
                                id="timeframe-dropdown",
                                options=[
                                    {"label": "1 Min", "value": "1 min"},
                                    {"label": "5 Min", "value": "5 min"},
                                    {"label": "15 Min", "value": "15 min"},
                                    {"label": "30 Min", "value": "30 min"},
                                    {"label": "1 Hour", "value": "1 hour"},
                                    {"label": "1 Day", "value": "1 day"},
                                ],
                                value=default_timeframe,
                                clearable=False,
                                searchable=False,
                                className="black-dropdown",
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(id="dashboard-metrics-strip", className="metrics-strip"),
            html.Div(id="load-status-text", className="status-text"),
            html.Div(
                className="range-row chart-control-row",
                children=make_chart_control_buttons("dashboard"),
            ),
            html.Div(
                className="chart-card",
                children=[
                    dcc.Graph(
                        id="live-chart",
                        className="chart-graph",
                        config=CHART_CONFIG,
                    ),
                ],
            ),
            html.Div(id="dashboard-stats-grid", className="stats-grid"),
        ],
    )


def _build_strategy_lab_panel():
    return html.Div(
        className="strategy-lab-panel watch-workspace-panel",
        children=[
            html.Div(
                className="strategy-lab-header",
                children=[
                    html.Div("Strategy Lab", className="strategy-lab-title"),
                    html.Div(
                        "Indicator script only · No auto-trading yet",
                        className="strategy-lab-subtitle",
                    ),
                ],
            ),
            dcc.Textarea(
                id="strategy-script-input",
                value=(
                    "fast = sma(close, 9)\n"
                    "slow = ema(close, 21)\n"
                    "\n"
                    "buy when crossover(fast, slow)\n"
                    "sell when crossunder(fast, slow)\n"
                    "\n"
                    "plot fast\n"
                    "plot slow"
                ),
                placeholder=(
                    "Example:\n"
                    "fast = sma(close, 9)\n"
                    "slow = ema(close, 21)\n"
                    "\n"
                    "buy when crossover(fast, slow)\n"
                    "sell when crossunder(fast, slow)\n"
                    "\n"
                    "plot fast\n"
                    "plot slow"
                ),
                className="strategy-script-input",
            ),
            html.Div(
                className="strategy-lab-actions",
                children=[
                    html.Button(
                        "Run Script",
                        id="strategy-run",
                        n_clicks=0,
                        className="strategy-run-btn",
                    ),
                    html.Button(
                        "Clear",
                        id="strategy-clear",
                        n_clicks=0,
                        className="strategy-clear-btn",
                    ),
                ],
            ),
            html.Div(
                id="strategy-status",
                className="strategy-status",
                children="Strategy Lab ready.",
            ),
            html.Div(
                className="strategy-backtest-panel",
                children=[
                    html.Div("Backtest", className="strategy-backtest-title"),
                    html.Div(
                        className="strategy-backtest-controls",
                        children=[
                            html.Div(
                                className="control-box strategy-backtest-input-box",
                                children=[
                                    html.Label("Initial Cash"),
                                    dcc.Input(
                                        id="backtest-initial-cash",
                                        type="number",
                                        min=100,
                                        step=100,
                                        value=100000,
                                        className="paper-input",
                                        debounce=True,
                                    ),
                                ],
                            ),
                            html.Div(
                                className="control-box strategy-backtest-input-box",
                                children=[
                                    html.Label("Quantity"),
                                    dcc.Input(
                                        id="backtest-quantity",
                                        type="number",
                                        min=1,
                                        step=1,
                                        value=10,
                                        className="paper-input",
                                        debounce=True,
                                    ),
                                ],
                            ),
                            html.Button(
                                "Run Backtest",
                                id="strategy-run-backtest",
                                n_clicks=0,
                                className="strategy-run-btn",
                            ),
                        ],
                    ),
                    html.Div(
                        id="backtest-status",
                        className="strategy-status",
                        children="Backtest ready.",
                    ),
                    html.Div(
                        id="backtest-results-panel",
                        className="backtest-results-panel",
                        children=[
                            html.Div("Run a backtest to see results.", className="paper-empty"),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="strategy-help-panel",
                children=[
                    html.Div(
                        className="strategy-help-header",
                        children=[
                            html.Div("Strategy Help", className="strategy-help-title"),
                            html.Div(
                                "Language guide, function reference, and examples",
                                className="strategy-help-subtitle",
                            ),
                        ],
                    ),
                    html.Div(
                        className="strategy-help-controls",
                        children=[
                            html.Div(
                                className="strategy-help-example-control",
                                children=[
                                    html.Label("Load Example"),
                                    dcc.Dropdown(
                                        id="strategy-example-dropdown",
                                        options=[
                                            {
                                                "label": "EMA Crossover",
                                                "value": "ema_crossover.txt",
                                            },
                                            {
                                                "label": "Fast SMA Test",
                                                "value": "sma_fast_test.txt",
                                            },
                                            {
                                                "label": "RSI Mean Reversion",
                                                "value": "rsi_mean_reversion.txt",
                                            },
                                            {
                                                "label": "Boolean Crossover",
                                                "value": "boolean_crossover.txt",
                                            },
                                            {
                                                "label": "EMA + ATR Filter",
                                                "value": "ema_supertrend.txt",
                                            },
                                            {
                                                "label": "Background Regime Filter",
                                                "value": "background_regime_test.txt",
                                            },
                                        ],
                                        value="ema_crossover.txt",
                                        clearable=False,
                                        searchable=False,
                                        className="black-dropdown",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="strategy-help-button-row",
                                children=[
                                    html.Button(
                                        "Insert Example",
                                        id="strategy-insert-example",
                                        n_clicks=0,
                                        className="strategy-run-btn strategy-help-btn",
                                    ),
                                    html.Button(
                                        "Language Guide",
                                        id="strategy-show-language-guide",
                                        n_clicks=0,
                                        className="range-btn strategy-help-btn",
                                    ),
                                    html.Button(
                                        "Function Reference",
                                        id="strategy-show-function-reference",
                                        n_clicks=0,
                                        className="range-btn strategy-help-btn",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        id="strategy-help-content",
                        className="strategy-help-content",
                        children=[
                            html.Div(
                                "Choose an example, insert it into the editor, or open the language guide.",
                                className="paper-empty",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _build_paper_trading_panel():
    return html.Div(
        className="paper-trading-panel watch-workspace-panel",
        children=[
            html.Div(
                className="paper-panel-header",
                children=[
                    html.Div("Paper Trading", className="paper-panel-title"),
                    html.Div(
                        "Simulated only · No IBKR live orders",
                        className="paper-panel-subtitle",
                    ),
                ],
            ),
            html.Div(
                className="paper-controls-row",
                children=[
                    html.Div(
                        className="control-box control-qty",
                        children=[
                            html.Label("Quantity"),
                            dcc.Input(
                                id="paper-order-qty",
                                type="number",
                                min=1,
                                step=1,
                                value=1,
                                className="paper-input",
                                debounce=True,
                            ),
                        ],
                    ),
                    html.Div(
                        className="paper-control-row",
                        children=[
                            html.Div(
                                className="paper-control-group",
                                children=[
                                    html.Label("Price Source", className="paper-control-label"),
                                    dcc.RadioItems(
                                        id="paper-price-source",
                                        options=[
                                            {"label": "Replay", "value": "replay"},
                                            {"label": "Live", "value": "live"},
                                        ],
                                        value="replay",
                                        inline=True,
                                        className="paper-radio",
                                        inputClassName="paper-radio-input",
                                        labelClassName="paper-radio-label",
                                    ),
                                ],
                            ),
                            html.Div(
                                className="paper-control-group",
                                children=[
                                    html.Label("Position Mode", className="paper-control-label"),
                                    dcc.RadioItems(
                                        id="paper-position-mode",
                                        options=[
                                            {"label": "Long Only", "value": "long_only"},
                                            {"label": "Allow Shorts", "value": "allow_shorts"},
                                        ],
                                        value="long_only",
                                        inline=True,
                                        className="paper-radio",
                                        inputClassName="paper-radio-input",
                                        labelClassName="paper-radio-label",
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        className="paper-button-group",
                        children=[
                            html.Button("Buy", id="paper-buy", n_clicks=0, className="paper-buy-btn"),
                            html.Button("Sell", id="paper-sell", n_clicks=0, className="paper-sell-btn"),
                            html.Button(
                                "Short Buy",
                                id="paper-short-buy",
                                n_clicks=0,
                                className="paper-btn paper-short-btn hidden",
                            ),
                            html.Button(
                                "Short Sell",
                                id="paper-short-sell",
                                n_clicks=0,
                                className="paper-btn paper-short-btn hidden",
                            ),
                            html.Button(
                                "Reset Paper",
                                id="paper-reset",
                                n_clicks=0,
                                className="paper-reset-btn",
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                id="paper-trade-status",
                className="paper-trade-status",
                children="Paper account ready.",
            ),
            html.Div(
                className="paper-summary-grid",
                children=[
                    html.Div(id="paper-summary-panel", className="paper-summary-panel"),
                ],
            ),
            html.Div(
                className="paper-table-grid",
                children=[
                    html.Div(
                        className="paper-table-card",
                        children=[
                            html.Div("Positions", className="paper-table-title"),
                            html.Div(id="paper-positions-panel"),
                        ],
                    ),
                    html.Div(
                        className="paper-table-card",
                        children=[
                            html.Div("Orders", className="paper-table-title"),
                            html.Div(id="paper-orders-panel"),
                        ],
                    ),
                    html.Div(
                        className="paper-table-card",
                        children=[
                            html.Div("Fills", className="paper-table-title"),
                            html.Div(id="paper-fills-panel"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _build_trade_analytics_panel():
    return html.Div(
        className="trade-analytics-panel watch-workspace-panel",
        children=[
            html.Div(
                className="trade-analytics-panel-header",
                children=[
                    html.Div("Trade Analytics", className="trade-analytics-title"),
                    html.Div(
                        "Paper trading performance summary",
                        className="trade-analytics-subtitle",
                    ),
                ],
            ),
            html.Div(
                id="trade-analytics-content",
                className="trade-analytics-content trade-analytics-content-tabbed",
                children=[
                    html.Div("No analytics loaded yet.", className="paper-empty"),
                ],
            ),
        ],
    )


def _build_watch_workspace_tabs():
    return dcc.Tabs(
        id="watch-workspace-tabs",
        value="strategy-lab",
        className="watch-workspace-tabs",
        children=[
            dcc.Tab(
                label="Strategy Lab",
                value="strategy-lab",
                className="watch-workspace-tab",
                selected_className="watch-workspace-tab-selected",
                children=[_build_strategy_lab_panel()],
            ),
            dcc.Tab(
                label="Paper Trading",
                value="paper-trading",
                className="watch-workspace-tab",
                selected_className="watch-workspace-tab-selected",
                children=[_build_paper_trading_panel()],
            ),
            dcc.Tab(
                label="Trade Analytics",
                value="trade-analytics",
                className="watch-workspace-tab",
                selected_className="watch-workspace-tab-selected",
                children=[_build_trade_analytics_panel()],
            ),
        ],
    )


def build_watch_tab(symbol_options, default_symbol, default_speed=1, default_index=100, default_date=None):
    return html.Div(
        className="tab-panel watch-tab-panel",
        children=[
            html.Div(
                className="controls-row controls-row-top",
                children=[
                    html.Div(
                        className="control-box control-symbol",
                        children=[
                            html.Label("Replay Symbol"),
                            dcc.Dropdown(
                                id="watch-symbol-dropdown",
                                options=symbol_options,
                                value=default_symbol,
                                placeholder="Search ticker, symbol, or company...",
                                clearable=False,
                                searchable=True,
                                className="black-dropdown",
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box control-timeframe",
                        children=[
                            html.Label("Interval"),
                            dcc.Dropdown(
                                id="watch-timeframe-dropdown",
                                options=[
                                    {"label": "1 Min", "value": "1 min"},
                                    {"label": "5 Min", "value": "5 min"},
                                    {"label": "15 Min", "value": "15 min"},
                                    {"label": "30 Min", "value": "30 min"},
                                    {"label": "1 Hour", "value": "1 hour"},
                                    {"label": "1 Day", "value": "1 day"},
                                ],
                                value="1 min",
                                clearable=False,
                                searchable=False,
                                className="black-dropdown",
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box control-timeframe control-speed",
                        children=[
                            html.Label("Speed"),
                            dcc.Dropdown(
                                id="replay-speed",
                                options= [
                                    {"label": "0.25x", "value": 0.25, "search": "0.25x quarter slow"},
                                            {"label": "0.5x", "value": 0.5, "search": "0.5x half slow"},
                                            {"label": "1x", "value": 1, "search": "1x normal default"},
                                            {"label": "2x", "value": 2, "search": "2x double fast"},
                                            {"label": "5x", "value": 5, "search": "5x very fast"},
                                ],
                                value=default_speed,
                                clearable=False,
                                searchable=False,
                                className="black-dropdown",
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box control-timeframe",
                        children=[
                            html.Label("Replay Start"),
                            dcc.DatePickerSingle(
                                id="replay-date",
                                date=default_date,
                                display_format="MM/DD/YYYY",
                                max_date_allowed=date.today(),
                                disabled_days=make_disabled_weekend_days(),
                                className="date-picker-dark",
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box control-timeframe",
                        children=[
                            html.Label("Replay End"),
                            dcc.DatePickerSingle(
                                id="replay-end-date",
                                date=default_date,
                                display_format="MM/DD/YYYY",
                                max_date_allowed=date.today(),
                                disabled_days=make_disabled_weekend_days(),
                                className="date-picker-dark",
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box",
                        children=[
                            html.Label("Replay Range"),
                            html.Button(
                                "Load Range",
                                id="replay-load-range",
                                n_clicks=0,
                                className="range-btn",
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="controls-row controls-row-bottom",
                children=[
                    html.Div(
                        className="control-box",
                        children=[
                            html.Label("Playback"),
                            html.Div(
                                [
                                    html.Button("Play", id="replay-play", n_clicks=0),
                                    html.Button("Pause", id="replay-pause", n_clicks=0),
                                    html.Button("Forward", id="replay-step", n_clicks=0),
                                    html.Button("Rewind", id="replay-rewind", n_clicks=0),
                                ],
                                style={"display": "flex", "gap": "8px", "flexWrap": "wrap"},
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box control-symbol slider-box",
                        children=[
                            html.Label("Position"),
                            dcc.Slider(
                                id="replay-slider",
                                min=1,
                                max=100,
                                step=1,
                                value=default_index,
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(id="watch-metrics-strip", className="metrics-strip"),
            html.Div(id="watch-status", className="status-text"),
            html.Div(
                className="range-row chart-control-row",
                children=make_chart_control_buttons("watch"),
            ),
            html.Div(
                className="chart-card watch-chart-wrap",
                children=[
                    html.Div(
                        id="watch-loading-overlay",
                        className="watch-loading-overlay",
                        children=[
                            html.Div("Preparing replay data...", className="watch-loading-text"),
                        ],
                    ),
                    dcc.Graph(
                        id="watch-chart",
                        className="chart-graph",
                        config=CHART_CONFIG,
                    ),
                ],
            ),
            html.Div(id="watch-stats-grid", className="stats-grid"),
            _build_watch_workspace_tabs(),
        ],
    )

def build_charts_tab(symbol_options, timeframe_map, default_symbol, default_timeframe):
    return html.Div(
        className="tab-panel charts-tab-panel",
        children=[
            html.Div(
                className="controls-row",
                children=[
                    html.Div(
                        className="control-box control-symbol",
                        children=[
                            html.Label("Instrument"),
                            dcc.Dropdown(
                                id="charts-symbol-dropdown",
                                options=symbol_options,
                                value=default_symbol,
                                placeholder="Search ticker, symbol, or company...",
                                searchable=True,
                                clearable=False,
                                className="black-dropdown",
                            ),
                        ],
                    ),
                    html.Div(
                        className="control-box control-timeframe",
                        children=[
                            html.Label("Interval"),
                            dcc.Dropdown(
                                id="charts-timeframe-dropdown",
                                options=[
                                    {"label": "1 Min", "value": "1 min"},
                                    {"label": "5 Min", "value": "5 min"},
                                    {"label": "15 Min", "value": "15 min"},
                                    {"label": "30 Min", "value": "30 min"},
                                    {"label": "1 Hour", "value": "1 hour"},
                                    {"label": "1 Day", "value": "1 day"},
                                ],
                                value=default_timeframe,
                                clearable=False,
                                searchable=False,
                                className="black-dropdown",
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(id="chart-metrics-strip", className="metrics-strip"),
            html.Div(id="charts-status", className="status-text"),
            html.Div(
                className="range-row chart-control-row",
                children=make_chart_control_buttons("charts"),
            ),
            html.Div(
                className="chart-card",
                children=[
                    dcc.Graph(
                        id="charts-main-graph",
                        className="chart-graph",
                        config=CHART_CONFIG,
                    ),
                ],
            ),
            html.Div(id="charts-stats-grid", className="stats-grid"),
        ],
    )
