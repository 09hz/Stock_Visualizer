import pandas as pd
import pandas_ta as ta
from lightweight_charts import Chart
import yfinance as yf

if __name__ == '__main__':

    chart = Chart()

    gold = yf.Ticker("GC=F")
    df = gold.history(period="max", interval="1d")
    #msft = yf.Ticker('MSFT')
    #df = msft.history(period = "1y")


    #Indicator Values
    sma = df.ta.sma(length=20).to_frame()
    sma = sma.reset_index()
    sma = sma.rename(columns={"Date" : "time", "SMA_20" :"value" })
    sma = sma.dropna()

    # Columns: time, open, high, low, close, volume
    df =df.reset_index()
    df.columns = df.columns.str.lower()

    #SMA Line
    line = chart.create_line()
    line.set(sma)
    chart.set(df)

    #chart.watermark("MSFT")
    chart.watermark("Gold")
    chart.show(block = True)