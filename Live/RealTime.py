import time
import pandas as pd
from ib_insync import IB, Stock, util
from lightweight_charts import Chart


def get_ib_history(ib, contract):
    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr='3000 S',
        barSizeSetting='1 min',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=2
    )

    df = util.df(bars)
    if df.empty:
        return df

    # Keep only OHLCV and use datetime index
    df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
    df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert(None)
    df = df.sort_values('date').drop_duplicates(subset='date')
    df = df.set_index('date')

    return df


def get_latest_bar(ib, contract):
    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr='120 S',
        barSizeSetting='1 min',
        whatToShow='TRADES',
        useRTH=True,
        formatDate=2
    )

    df = util.df(bars)
    if df.empty:
        return None

    df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
    df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_convert(None)
    df = df.sort_values('date').drop_duplicates(subset='date')

    last = df.iloc[-1].copy()
    last.name = last['date']   # keep time in the Series index/name
    last = last.drop(labels=['date'])
    return last


if __name__ == '__main__':
    ib = IB()
    ib.connect('127.0.0.1', 4001, clientId=1, timeout=30)

    stock = Stock('AAPL', 'SMART', 'USD', primaryExchange='NASDAQ')
    ib.qualifyContracts(stock)

    hist = get_ib_history(ib, stock)

    print(hist.head())
    print(hist.tail())
    print(hist.index.dtype)
    print(hist.columns.tolist())

    if hist.empty:
        raise RuntimeError("No historical data returned from IB.")

    chart = Chart()
    chart.set(hist)
    chart.watermark("AAPL")
    chart.show(block=False)

    last_seen = hist.index[-1]

    while True:
        try:
            bar = get_latest_bar(ib, stock)
            if bar is not None:
                # always update the current/latest 1-minute bar
                chart.update(bar)

                if bar.name != last_seen:
                    print("New bar:", bar.name, float(bar['close']))
                    last_seen = bar.name

            ib.sleep(5)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print("Update error:", e)
            ib.sleep(5)