import plotly.graph_objects as go
import pandas as pd
from ib_insync import IB, Stock, util

ib = IB()
ib.connect('127.0.0.1', 4001, clientId=1)

bars = ib.reqHistoricalData(
    Stock('MSFT', 'SMART', 'USD'),
    endDateTime='', durationStr='3000 S',
    barSizeSetting='1 min', whatToShow='MIDPOINT', useRTH=True)

df = util.df(bars)
df = df.reset_index()
df.columns = df.columns.str.lower()
df = df.rename(columns={'date': 'time'})

fig = go.Figure(data=[go.Candlestick(
    x=df['time'],
    open=df['open'],
    high=df['high'],
    low=df['low'],
    close=df['close']
)])

fig.update_layout(title='MSFT 1-min', xaxis_rangeslider_visible=False)
fig.show()

ib.disconnect()