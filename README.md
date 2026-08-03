# Stock Visualizer Live

A local Python/Dash trading research platform for real-time stock visualization, historical replay, paper trading, strategy scripting, backtesting, and chart overlays.

> **Status:**  This application is for education, research, and strategy development only. It is not financial advice and is not intended for unattended live trading.

---

## Overview

Stock Visualizer Live provides a desktop-style browser interface for studying stock price action, replaying historical sessions, testing strategy logic, and simulating paper trades.

The app includes:

- A Dash-based UI with Dashboard, Watch/Replay, Strategy Lab, Paper Trading, and Analytics workspaces.
- Interactive Brokers integration for live and historical market data.
- A replay engine for single-day and multi-day historical sessions.
- Replay cache validation to detect partial or incomplete historical data.
- A Pine-inspired strategy scripting language.
- Indicator overlays, buy/sell markers, and background regime shading.
- A simple long-only backtesting engine.
- A local paper-trading broker and trade analytics view.
- A modular service/rendering architecture designed for future expansion.

This repository is intended to demonstrate the architecture and engineering behind a trading research tool, not to provide production trading infrastructure.

---

## Important Requirements

### Interactive Brokers account required

This app is designed to work with **Interactive Brokers (IBKR)** through Trader Workstation (TWS) or IB Gateway.

To use live and historical market data through the app, you generally need:

1. An approved Interactive Brokers account.
2. TWS or IB Gateway running locally.
3. API access enabled in TWS/IB Gateway.
4. Market data permissions/subscriptions for the symbols and exchanges you want to load.
5. Sufficient account funding to meet IBKR market-data/API requirements.


### Market data subscription required

The app depends on IBKR historical and/or streaming market data. If your account does not have the correct market data subscription, the app may:

- Return delayed data
- Return partial data
- Return no bars
- Fail to load replay sessions
- Show empty charts

Market data access is controlled by IBKR and the exchanges. Subscriptions, costs, and permissions can vary by region, account type, asset class, exchange, and professional/non-professional status.

### Funding / deposit note

Interactive Brokers currently lists no account minimum for many individual account types, but IBKR API market-data documentation states that API market data has additional requirements, including an opened IBKR account, IBKR Pro, and a funded account amount in addition to market data subscription costs.

For practical use of this project with IBKR API market data, the recommended minimum is:

```text
Recommended practical minimum: USD 500+
```

Do **not** assume that a USD 200 deposit is enough for API market data access. IBKR requirements can change, so check the current IBKR documentation and your Client Portal account status.

Useful IBKR references:

- Interactive Brokers Required Minimums: https://www.interactivebrokers.com/en/accounts/required-minimums.php
- IBKR API Market Data Subscriptions: https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/
- IBKR Market Data Pricing: https://www.interactivebrokers.com/en/pricing/market-data-pricing.php

---

## Features

### Dashboard

- Live stock chart
- Symbol selector
- Timeframe selector
- Price metrics
- Basic session statistics
- Live snapshot display through the IBKR adapter

### Watch / Replay

- Load a single replay date
- Load and stitch replay date ranges
- Play, pause, step, rewind, and manual seek
- Replay speed control
- Raw 1-minute replay data with resampled chart display
- Cache validation for incomplete historical sessions
- Holiday/no-session handling to avoid saving empty replay data

### Strategy Lab

The Strategy Lab includes a Pine-inspired script language.

Example strategy:

```text
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)

bullCross = ta.crossover(fast, slow)
bearCross = ta.crossunder(fast, slow)

plot fast
plot slow

buy when bullCross
sell when bearCross
```

Background regime example:

```text
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)

bullMarket = fast > slow
bearMarket = fast < slow

bgcolor bullMarket color="green"
bgcolor bearMarket color="red"

plot fast
plot slow

buy when ta.crossover(fast, slow)
sell when ta.crossunder(fast, slow)
```

Supported strategy concepts include:

- `ta.sma`
- `ta.ema`
- `ta.rsi`
- `ta.atr`
- `ta.highest`
- `ta.lowest`
- `ta.crossover`
- `ta.crossunder`
- Boolean expressions
- Series-to-series comparisons
- Session/time filters
- Buy/sell signal rules
- `plot`
- `bgcolor`

### Backtesting

- Runs strategy signals against loaded replay data
- Uses the full loaded replay dataset where available
- Tracks equity/PnL results
- Displays trades, win rate, return, and drawdown metrics

### Paper Trading

- Simulated buy/sell actions
- Position tracking
- Order/fill history
- Trade analytics
- Chart markers when replay is paused or redrawn
- Local persistence under the paper cache directory

### Replay Cache Validation

Replay data is cached locally to avoid unnecessary IBKR requests.

The cache validation guard checks:

- Whether cached data exists
- Whether the historical session has enough bars
- Whether cached bars start near the regular market open
- Whether cached bars end near the regular market close
- Whether the selected day is likely a holiday/no-session day

Incomplete historical cache data is invalidated and refetched. Empty holiday/no-session data should not overwrite valid cache data.

---

## Tech Stack

- Python 3.13
- Dash
- Plotly
- pandas
- ib_async / Interactive Brokers API
- HTML/CSS
- Git

---

## Repository Layout

```text
Stock_Visualizer/
Live/
├── assets/
│   └── style.css
├── cache/
│   ├── paper/
│   └── replay/
├── core/
│   ├── BackTestEngine.py
│   ├── IndicatorEngine.py
│   ├── PaperBroker.py
│   ├── RealTime.py
│   ├── ReplayModule.py
│   ├── RiskGuard.py
│   ├── StrategyEngine.py
│   └── StrategyFunctionRegistry.py
├── data/
│   ├── nasdaq_symbol_names_filled.csv
│   └── nasdaq_tickers_simple.txt
├── docs/
│   ├── architecture/
│   ├── catalog/
│   ├── patches/
│   ├── strategy_examples/
│   ├── CHANGELOG_DEV.md
│   ├── requirements.txt
│   └── STRATEGY_LANGUAGE.md
├── models/
│   └── watch_models.py
├── renderers/
│   ├── strategy_overlay_renderer.py
│   └── watch_chart_renderer.py
├── reports/
│   └── report_builder.py
├── services/
│   ├── backtest_service.py
│   ├── bar_store.py
│   ├── bar_view_service.py
│   ├── chart_viewport_service.py
│   ├── paper_cache.py
│   ├── paper_trading_service.py
│   ├── replay_service.py
│   ├── strategy_overlay_service.py
├── ui/
│   └── tabs_ui.py
├── utils/
│   └── chart_utils.py
├── .gitignore
├── app.py
├── callbacks.py
├── config.py
└── README.md
```

---

## Setup

Recommended interpreter: **64-bit Python 3.13**.

A virtual environment is not required. If Python 3.13 is installed as your
system or user interpreter, install the dependencies with that interpreter and
run the application directly. An optional virtual-environment workflow is
included below for contributors who want dependency isolation.

### 1. Clone the repository

```bash
git clone https://github.com/09hz/Stock_Visualizer.git
cd Stock_Visualizer
```

### 2. Verify Python 3.13

Windows PowerShell:

```powershell
py -3.13 --version
```

macOS/Linux:

```bash
python3.13 --version
```

The command should report Python `3.13.x`.

### 3. Install dependencies

The repository's requirements file is located at
`Live/docs/requirements.txt`.

Windows PowerShell:

```powershell
py -3.13 -m pip install --upgrade pip
py -3.13 -m pip install -r Live/docs/requirements.txt
```

macOS/Linux:

```bash
python3.13 -m pip install --upgrade pip
python3.13 -m pip install -r Live/docs/requirements.txt
```

This installs Dash, Plotly, pandas, `ib_async`, replay-cache support, and the
other packages used by the application. `pyarrow` is already included in the
requirements file.

### Optional: use a virtual environment

You can skip this section when using your installed Python 3.13 interpreter.
To keep project packages isolated, create the environment with Python 3.13:

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r Live/docs/requirements.txt
```

macOS/Linux:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r Live/docs/requirements.txt
```

## Interactive Brokers / TWS Setup

1. Install and open **Trader Workstation (TWS)** or **IB Gateway**.
2. Log in with your Interactive Brokers account.
3. Enable API access:
   - TWS: `File > Global Configuration > API > Settings`
   - Enable socket clients.
   - Set or confirm the socket port.
4. The current application configuration uses `127.0.0.1:4001` in
   `Live/app.py`. Configure TWS/IB Gateway to accept API connections on port
   `4001`, or change the port in `Live/app.py` to match your installation.
5. Common IBKR defaults for reference:
   - Paper TWS: `7497`
   - Live TWS: `7496`
   - Paper IB Gateway: `4002`
   - Live IB Gateway: `4001`
6. Add `127.0.0.1` as a trusted IP if required.
7. Confirm your IBKR account has the market data subscriptions needed for the symbols you want to load.

---

## Running the App

From the project root, using the system/user Python 3.13 interpreter:

Windows PowerShell:

```powershell
py -3.13 Live/app.py
```

macOS/Linux:

```bash
python3.13 Live/app.py
```

If an activated virtual environment is being used, run `python Live/app.py`
instead.

Then open:

```text
http://127.0.0.1:8050
```

---

## Basic Workflows

### 1. Quick start

```powershell
py -3.13 Live/app.py
```

Then in the browser:

1. Open the app at `http://127.0.0.1:8050`.
2. Go to the Dashboard tab.
3. Select a symbol or use the default.
4. Open the Watch tab.
5. Select a replay date or date range.
6. Click Load.
7. Press Play, Pause, Step, Rewind, or drag the replay slider.

### 2. Replay a single trading day

1. Open the Watch tab.
2. Select a symbol.
3. Choose a replay date.
4. Click Load.
5. Press Play.

For historical dates, ReplayService filters for the regular session window and validates completeness before using cached data.

### 3. Load a date range

1. Open the Watch tab.
2. Select a start date and end date.
3. Click Load Range.
4. The service loads/stitches valid trading days into the replay engine.
 
Notice! For periods longer than a month, time to recover data visually will take a long time! 
Back testing works independently so even if the data has not loaded on the chart you still can run the backtest!

### 4. Run a strategy

1. Open the Watch tab.
2. Open the Strategy Lab workspace.
3. Paste or insert a strategy script.
4. Click Run Strategy.
5. Indicators/signals/backgrounds appear on the Watch chart.
6. Use Backtest to evaluate the strategy over the loaded replay data.

### 5. Paper trade

1. Open the Watch tab.
2. Open the Paper Trading workspace.
3. Choose replay or live price source.
4. Use Buy/Sell controls.
5. Review positions, orders, fills, and analytics.

---

## Programmatic Examples

### Load a replay range

```python
from core.RealTime import RealTimeIB
from core.ReplayModule import ReplayEngine
from services.replay_service import ReplayService

rt = RealTimeIB(host="127.0.0.1", port=7497)
rt.start("MSFT")

engine = ReplayEngine()
service = ReplayService(rt, engine)

stitched = service.load_date_range(
    symbol="MSFT",
    start_date="2026-06-01",
    end_date="2026-06-05",
    timeframe="1 min",
)

print("stitched rows", len(stitched))
print("engine info", engine.info())
```

### Run a backtest

```python
from core.BackTestEngine import BackTestEngine
from core.StrategyEngine import StrategyEngine

script = """
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)

buy when ta.crossover(fast, slow)
sell when ta.crossunder(fast, slow)
"""

bars = service.all_bars()

strategy_engine = StrategyEngine()
strategy_result = strategy_engine.run(script, bars)

backtest_engine = BackTestEngine()
result = backtest_engine.run(
    bars=bars,
    signals=strategy_result.signals,
    initial_cash=100000,
    quantity=1,
)

print(result)
```

---

## Documentation

Additional documentation can be organized under:

```text
Live/docs/
├── CHANGELOG_DEV.md
├── STRATEGY_LANGUAGE.md
├── architecture/
├── catalog/
├── patches/
├── strategy_examples/

```


---

## Troubleshooting

### IBKR connection errors

If the app cannot connect to Interactive Brokers:

- Confirm TWS or IB Gateway is running.
- Confirm API access is enabled.
- Confirm TWS/IB Gateway is listening on `127.0.0.1:4001`, or change the
  `RealTimeIB` port in `Live/app.py` to match your configured API socket port.
- Confirm trusted IP settings allow `127.0.0.1`.
- Confirm your IBKR session is logged in.

### Empty or partial charts

Possible causes:

- Missing market data subscription
- Incorrect symbol
- Market holiday/no-session day
- IBKR historical data limitation
- Incomplete replay cache that needs to be refreshed

The app prints diagnostic logs such as:

```text
[REPLAY CACHE]
[REPLAY CACHE MEMORY CHECK]
[REPLAY CACHE INVALIDATE MEMORY]
[IB HISTORY_AT RETURNED]
[LHR CHUNK DONE]
[REPLAY SOURCE RESULT]
```

### Parquet read/write errors

Install parquet support:

```bash
pip install pyarrow fastparquet
```

### Clearing cache

Replay cache:

```powershell
Remove-Item -Recurse -Force .\Live\cache\replay -ErrorAction SilentlyContinue
```

Paper trading cache:

```powershell
Remove-Item -Recurse -Force .\Live\cache\paper -ErrorAction SilentlyContinue
```

---

## Development Commands

Compile key files:

```bash
python -m py_compile Live/app.py
python -m py_compile Live/callbacks.py
python -m py_compile Live/core/RealTime.py
python -m py_compile Live/core/ReplayModule.py
python -m py_compile Live/core/StrategyEngine.py
python -m py_compile Live/services/replay_service.py
python -m py_compile Live/renderers/strategy_overlay_renderer.py
```

Run the app:

```bash
python Live/app.py
```

---

## Security and Safety

Do not commit:

```text
.env
Live/cache/
cache/
__pycache__/
*.pyc
*.log
IBKR credentials
account numbers
API keys
large historical data files
```

Recommended `.gitignore` entries:

```gitignore
.env
.env.*
__pycache__/
*.pyc
Live/cache/
cache/
*.log
.DS_Store
.vscode/
.idea/
```

This project includes local paper trading only. Do not wire real-money order execution without a complete audit of the trading code, broker integration, risk controls, logging, monitoring, and compliance requirements.

---

## Known Limitations

- This is a local research/demo application, not a production trading platform.
- IBKR market data permissions are required and may differ by account.
- Replay accuracy depends on the quality and availability of IBKR historical bars.
- Plotly rendering can become heavy with dense overlays.
- Background shading is intentionally limited during active replay playback for performance.
- The app currently focuses primarily on stocks.
- AI strategy generation is intentionally not included in this public/demo version.

---

## Roadmap

Planned future work:

- Client-side chart renderer proof of concept
- Dedicated paper marker renderer
- Background renderer extraction
- Multi-asset support
- Portfolio analytics
- AI-assisted strategy generation in a separate private branch/repo
- FastAPI/WebSocket backend exploration

---

## Disclaimer

This project is for educational and research purposes only. It is not financial advice, trading advice, or investment advice. Trading stocks, options, futures, forex, and other financial instruments involves risk. Use paper trading and backtesting first. Do not use this project for live trading without additional risk controls, testing, and compliance review.

Interactive Brokers, IBKR, TWS, and IB Gateway are trademarks or services of Interactive Brokers Group and/or its affiliates. This project is not affiliated with or endorsed by Interactive Brokers.
