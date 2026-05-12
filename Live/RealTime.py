import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from ib_insync import IB, Stock, util
from datetime import datetime

ib = IB()
ib.connect('127.0.0.1', 4001, clientId=1)

SYMBOL = 'MSFT'
contract = Stock(SYMBOL, 'SMART', 'USD', primaryExchange='NASDAQ')
ib.qualifyContracts(contract)

bars = ib.reqHistoricalData(
    contract,
    endDateTime='',
    durationStr='1 D',
    barSizeSetting='1 min',
    whatToShow='TRADES',
    useRTH=True
)

df = util.df(bars)
df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
df = df.rename(columns={'date': 'time'})
df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
df['volume'] = df['volume'].fillna(0).astype(int)

current_price = df.iloc[-1]['close']
day_high = df['high'].max()
day_low = df['low'].min()
day_open = df.iloc[0]['open']

ticker = ib.reqMktData(contract, '', False, False)

last_bar_time = pd.to_datetime(df.iloc[-1]['time']).to_pydatetime().replace(second=0, microsecond=0)
last_bar_index = len(df) - 1
tick_count = 0


def create_chart():
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        row_heights=[0.7, 0.3]
    )

    fig.add_trace(
        go.Candlestick(
            x=df['time'],
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name=SYMBOL
        ),
        row=1, col=1
    )

    colors = ['green' if c >= o else 'red' for c, o in zip(df['close'], df['open'])]

    fig.add_trace(
        go.Bar(
            x=df['time'],
            y=df['volume'],
            marker_color=colors,
            name='Volume',
            showlegend=False
        ),
        row=2, col=1
    )

    fig.update_layout(
        template='plotly_dark',
        xaxis_rangeslider_visible=False,
        height=800
    )
    return fig


fig = create_chart()
fig.write_html('trading_chart.html')

def on_tick(ticker, *args):
    global last_bar_time, last_bar_index, tick_count, current_price, day_high, day_low, df

    if ticker.last is None:
        return

    current_price = float(ticker.last)
    current_minute = datetime.now().replace(second=0, microsecond=0)

    day_high = max(day_high, current_price)
    day_low = min(day_low, current_price)

    last_size = ticker.lastSize if ticker.lastSize is not None else 0

    if current_minute == last_bar_time:
        df.at[last_bar_index, 'close'] = current_price
        df.at[last_bar_index, 'high'] = max(df.at[last_bar_index, 'high'], current_price)
        df.at[last_bar_index, 'low'] = min(df.at[last_bar_index, 'low'], current_price)
        df.at[last_bar_index, 'volume'] += last_size
    else:
        new_bar = pd.DataFrame({
            'time': [current_minute],
            'open': [current_price],
            'high': [current_price],
            'low': [current_price],
            'close': [current_price],
            'volume': [last_size]
        })
        df = pd.concat([df, new_bar], ignore_index=True)
        last_bar_time = current_minute
        last_bar_index = len(df) - 1

    tick_count += 1

    if tick_count % 10 == 0:
        fig = create_chart()
        fig.write_html('trading_chart.html')

ticker.updateEvent += on_tick

try:
    ib.run()
except KeyboardInterrupt:
    ib.disconnect()