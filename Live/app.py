from __future__ import annotations

import sys
import asyncio
from datetime import date

from dash import Dash, dcc, html

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from callbacks import register_callbacks
from config import (
    APP_TITLE,
    DEFAULT_REPLAY_INDEX,
    DEFAULT_REPLAY_SPEED,
    DEFAULT_SYMBOL,
    DEFAULT_TIMEFRAME,
    UI_INTERVAL_MS,
)
from core.RealTime import RealTimeIB, TIMEFRAME_MAP
from core.ReplayModule import ReplayEngine
from services.replay_service import ReplayService
from services.paper_cache import PaperStateCache
from ui.tabs_ui import (
    build_dashboard_tab,
    build_watch_tab,
    build_charts_tab,
)


try:
    from services.paper_trading_service import PaperTradingService
    from core.PaperBroker import PaperBroker
    from core.RiskGuard import RiskGuard
except Exception:
    PaperTradingService = None
    PaperBroker = None
    RiskGuard = None


rt = RealTimeIB(host="127.0.0.1", port=4001)
rt.start(DEFAULT_SYMBOL, DEFAULT_TIMEFRAME)

replay_engine = ReplayEngine()
replay_service = ReplayService(rt, replay_engine)

paper_trading_service = None
paper_state_cache = PaperStateCache(cache_dir="cache/paper")

if PaperTradingService and PaperBroker and RiskGuard:
    paper_trading_service = PaperTradingService(
        broker=PaperBroker(
            starting_cash=100_000,
            commission_per_order=0.0,
            slippage_bps=1.0,
        ),
        risk_guard=RiskGuard(
            allowed_symbols=None,
            max_quantity=1_000,
            max_notional=25_000,
            allow_short=False,
            live_trading_enabled=False,
        ),
    )

SYMBOL_OPTIONS = rt.get_symbol_options()

app = Dash(__name__, suppress_callback_exceptions=True)
app.title = APP_TITLE

app.layout = html.Div(
    className="app-shell",
    children=[
        html.Div(
            className="topbar",
            children=[
                html.Div(id="pair-title", className="pair-title"),
                html.Div(id="quote-strip", className="quote-strip"),
            ],
        ),
        dcc.Tabs(
            id="main-tabs",
            value="dashboard",
            className="main-tabs",
            children=[
                dcc.Tab(
                    label="Dashboard",
                    value="dashboard",
                    className="main-tab",
                    selected_className="main-tab-selected",
                    children=[
                        build_dashboard_tab(
                            symbol_options=SYMBOL_OPTIONS,
                            timeframe_map=TIMEFRAME_MAP,
                            default_symbol=DEFAULT_SYMBOL,
                            default_timeframe=DEFAULT_TIMEFRAME,
                        )
                    ],
                ),
                dcc.Tab(
                    label="Watch",
                    value="watch",
                    className="main-tab",
                    selected_className="main-tab-selected",
                    children=[
                        build_watch_tab(
                            symbol_options=SYMBOL_OPTIONS,
                            default_symbol=DEFAULT_SYMBOL,
                            default_speed=DEFAULT_REPLAY_SPEED,
                            default_index=DEFAULT_REPLAY_INDEX,
                            default_date=date.today().isoformat(),
                        )
                    ],
                ),
                dcc.Tab(
                    label="Charts",
                    value="charts",
                    className="main-tab",
                    selected_className="main-tab-selected",
                    children=[
                        build_charts_tab(
                            symbol_options=SYMBOL_OPTIONS,
                            timeframe_map=TIMEFRAME_MAP,
                            default_symbol=DEFAULT_SYMBOL,
                            default_timeframe=DEFAULT_TIMEFRAME,
                        )
                    ],
                ),
            ],
        ),

        # General UI/live refresh.
        dcc.Interval(id="ui-interval", interval=UI_INTERVAL_MS, n_intervals=0),

        # Dedicated replay heartbeat. This drives Play/Pause independently
        # from the general UI interval.
        dcc.Interval(id="replay-clock", interval=250, n_intervals=0),

        # Replay render trigger. Buttons/clock bump this store so the Watch chart
        # redraws without the slider callback fighting the clock.
        dcc.Store(id="replay-render-trigger", data=0),

        # Dedicated quotes heartbeat for independent updates while on Quotes tab.
        dcc.Interval(id="quotes-interval", interval=UI_INTERVAL_MS, n_intervals=0),

        dcc.Store(
            id="watch-load-request",
            data={
                "nonce": 0,
                "symbol": DEFAULT_SYMBOL,
                "replay_date": None,
                "timeframe": "1 min",
            },
        ),

        dcc.Store(
            id="dashboard-chart-state",
            data={
                "mode": "live",
                "range_key": "1D",
                "x_range": None,
                "y_range": None,
            },
        ),
        dcc.Store(
            id="watch-chart-state",
            data={
                "mode": "live",
                "range_key": "1D",
                "x_range": None,
                "y_range": None,
            },
        ),
        dcc.Store(
            id="charts-chart-state",
            data={
                "mode": "live",
                "range_key": "1D",
                "x_range": None,
                "y_range": None,
            },
        ),
 
        dcc.Store(id="paper-trade-trigger", data=0),
        dcc.Store(
            id="strategy-script-store",
            data={
                "script": "",
                "enabled": False,
                "nonce": 0,
            },
        ),
        dcc.Store(id="zoom-state", data={}),
        dcc.Store(id="active-symbol", data=DEFAULT_SYMBOL),
        dcc.Store(id="load-status", data="Ready"),
        dcc.Store(id="quotes-list", data=[]),
        dcc.Store(
            id="dashboard-state",
            data={
                "symbol": DEFAULT_SYMBOL,
                "timeframe": DEFAULT_TIMEFRAME,
            },
        ),
        dcc.Store(
            id="watch-state",
            data={
                "symbol": DEFAULT_SYMBOL,
                "replay_speed": DEFAULT_REPLAY_SPEED,
                "replay_index": DEFAULT_REPLAY_INDEX,
                "replay_date": None,
            },
        ),
    ],
)

register_callbacks(
    app,
    rt,
    replay_service,
    SYMBOL_OPTIONS,
    TIMEFRAME_MAP,
    paper_trading_service=paper_trading_service,
    paper_state_cache=paper_state_cache,
)

if __name__ == "__main__":
    app.run(debug=False)
