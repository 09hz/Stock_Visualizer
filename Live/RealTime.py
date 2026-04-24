import time
from ib_insync import*
from lightweight_charts import Chart
from yfinance.domain import market

if __name__ == '__main__':
    ib = IB()
    chart = Chart()

    #Connect to broker
    ib.connect('127.0.0.1', 7497, clientId=1)

    stock = Stock('APPL', 'SMART', 'USD')

    bars = ib.reqHistoricalData(
        stock, endDateTime= '' , durationStr='3000 S',
        barSizeSetting='1 min', whatToShow= 'MIDPOINT', useRTH= True
    )

    df = util.df(bars)

    chart = Chart(volume_emabled = False)
    chart.set(df)
    chart.show()

    market_data = ib.reqMktData(stock, '233', False, False)
    def onPendingTicker(ticker):
        print("pending ticker event received")
        for tick in ticker:
            ticks = util.df(tick.ticks)
            if ticks is not None:
                last_price = ticks(ticks['tickType'] == 4)
                if not last_price.empty:
                    print(last_price)
                    chart.update_from_tick(last_price.squeeze())
    ib.pendingTickersEvent += onPendingTicker
    ib.run()

    #Misery


