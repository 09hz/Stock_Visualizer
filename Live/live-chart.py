import pandas as pd
import pandas_ta as ta
import yfinance as yf
from lightweight_charts import Chart


if __name__ == '__main__':
    chart = Chart()

    msft = yf.Ticker("MSFT")
    df = msft.history(period="1y", interval="1d")

    #Indicator Values
    #sma = df.ta.sma(length=20).to_frame()
    #sma = sma.reset_index()
    #sma = sma.rename(columns={"Date" : "time", "SMA_20" :"value" })
    #sma = sma.dropna()


    df = df.reset_index()

    df = df.rename(columns={
        'Date': 'time',
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    })
    df = df[['time', 'open', 'high', 'low', 'close', 'volume']]
    df['time'] = pd.to_datetime(df['time']).dt.strftime('%Y-%m-%d')


    chart.set(df)

    #SMA Line
    #line = chart.create_line()
    #line.set(sma)

    chart.watermark("MSFT")
    chart.show(block = True)
