import time
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from lightweight_charts import Chart

def get_history(symbol ="MSFT", period = "5d", interval = "1m"):
    df = yf.Ticker(symbol).history(period=period, interval=interval)
    df = df.reset_index()
    time_col = 'Datetime' if 'Datetime' in df.columns else 'Date'

    df = df.rename(columns={
        time_col: 'time',
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    })
    df = df[['time', 'open', 'high', 'low', 'close', 'volume']].copy()
    df['time'] = pd.to_datetime(df['time']).dt.strftime('%Y-%m-%d %H:%M:%S')
    return df

def get_lastest_bar(symbol = 'MSFT'):
    df = yf.Ticker(symbol).history(period="1y", interval="1d")
    if df.empty:
        return None

    df = df.reset_index()
    time_col = 'Datetime' if 'Datetime' in df.columns else 'Date'
    df =df.rename(columns={
    time_col: 'time',
    'Open': 'open',
    'High': 'high',
    'Low': 'low',
    'Close': 'close',
    'Volume': 'volume'
    })

    df = df[['time', 'open', 'high', 'low', 'close', 'volume']].copy()
    last = df.iloc[-1].copy()
    last['time'] = pd.to_datetime(last['time']).strftime('%Y-%m-%d %H:%M:%S')
    return last



if __name__ == '__main__':
    symbol = 'MSFT'
    chart = Chart()

    hist = get_history(symbol)
    chart.set(hist)
    chart.show(block =False)
    last_seen_time = hist.iloc[-1]['time']

    while True:
        try:
            bar = get_lastest_bar(symbol = "MSFT")
            if bar is not None and bar['time']!= last_seen_time:
                chart.update(bar)
                last_seen_time = bar['time']
            time.sleep(5)

        except KeyboardInterrupt:
            break







    #Indicator Values
    #sma = df.ta.sma(length=20).to_frame()
    #sma = sma.reset_index()
    #sma = sma.rename(columns={"Date" : "time", "SMA_20" :"value" })
    #sma = sma.dropna()

    #SMA Line
    #line = chart.create_line()
    #line.set(sma)


