# Stock Viewer

A Python-based stock viewer built with `lightweight_charts`, `yfinance`, `pandas`, `pandas_ta`, and `ib_insync` for displaying historical and live-ish market data, with future plans for broker-connected trade execution.

## Overview

This project is a stock visualization tool designed to load and display market data from multiple sources:

- `yfinance` for historical and delayed market data
- `Interactive Brokers` through `ib_insync` for broker-connected market data
- local CSV files for testing and development

The current focus is on building a reliable charting and data display workflow. The long-term goal is to expand the project into a more complete trading application that can eventually support trade execution.

## Features

- Display OHLCV stock data in `lightweight_charts`
- Load historical stock data from `yfinance`
- Add technical indicators such as SMA with `pandas_ta`
- Load chart data from CSV files
- Connect to Interactive Brokers for broker-based data
- Work toward live or live-ish chart updates on a 1-minute interval
- Provide a foundation for future automated or manual trade execution

## Project Goals

### Current goals
- Build a clean stock viewer for charting market data
- Support multiple data sources
- Make chart updates work reliably for minute-based data
- Improve formatting and compatibility between data providers and chart input requirements

### Future goals
- Add real-time broker-connected updates
- Add indicator overlays and multiple chart tools
- Support order entry and execution through a broker
- Build a more complete trading dashboard

## Technologies Used

- Python
- pandas
- pandas_ta
- yfinance
- lightweight_charts
- ib_insync

## Example Use Cases

### 1. View historical data from yfinance
Load stock data such as `MSFT` and display it as a candlestick chart.

### 2. Overlay indicators
Calculate and display indicators such as a 20-period simple moving average.

### 3. Load from CSV
Use local OHLCV data for testing chart behavior and formatting.

### 4. Connect to Interactive Brokers
Request broker market data and historical bars, then update the chart dynamically.

## Repository Structure

You can organize the repo like this:

```text
stock-viewer/
│
├── live/
│   ├── live_chart.py
│   └── ib_live_chart.py
│
├── examples/
│   ├── yf_chart.py
│   ├── sma_chart.py
│   └── csv_chart.py
│
├── data/
│   └── ohlcv.csv
│
├── requirements.txt
└── README.md
