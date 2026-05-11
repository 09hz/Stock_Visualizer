"""
Interactive Trading Dashboard using Plotly Dash + ib_insync
- Select any symbol, time interval
- View candlestick charts with Plotly
- Place buy/sell orders through IB Gateway
"""

import dash
from dash import dcc, html, callback, Input, Output, State
import plotly.graph_objects as go
import pandas as pd
from ib_insync import IB, Stock, util
import threading
import queue

# Global IB connection (persistent across requests)
ib = None
chart_data_queue = queue.Queue()


# Connect to IB at startup (before Dash starts)
def init_ib():
    """Connect to IB Gateway/TWS once at startup"""
    global ib
    try:
        import random
        import time

        ib = IB()

        # Use a random client ID to avoid conflicts
        client_id = random.randint(1, 32767)

        # Disconnect any existing connection first
        try:
            ib.disconnect()
        except:
            pass

        ib.connect('127.0.0.1', 4001, clientId=client_id)

        # Start the event loop in a background thread
        # This allows async operations like reqHistoricalData to work
        ib.run(block=False)
        time.sleep(0.5)  # Give it a moment to start

        print(f"✓ Connected to IB Gateway (clientId={client_id})")
        return True, f"Connected to IB Gateway (ID={client_id})"
    except Exception as e:
        msg = f"Connection failed: {e}"
        print(f"✗ {msg}")
        return False, msg


# Initialize IB before app starts
ib_connected, ib_status = init_ib()


def fetch_bars(symbol, duration, interval):
    """Fetch historical bars from IB"""
    try:
        if not ib or not ib.isConnected():
            return None, "Not connected to IB"

        stock = Stock(symbol, 'SMART', 'USD')

        # Map interval names to IB format
        interval_map = {
            '1min': '1 min',
            '5min': '5 mins',
            '15min': '15 mins',
            '1hour': '1 hour',
            '1day': '1 day'
        }

        # reqHistoricalData is sync when called properly from event loop
        # ib.run(block=False) in init_ib() handles the event loop
        bars = ib.reqHistoricalData(
            stock,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=interval_map.get(interval, '1 min'),
            whatToShow='MIDPOINT',
            useRTH=True
        )

        if not bars or len(bars) == 0:
            return None, f"No data for {symbol} ({interval}, {duration})"

        # Convert to DataFrame
        df = util.df(bars)
        df = df.reset_index()
        df.columns = df.columns.str.lower()
        df = df.rename(columns={'date': 'time'})
        df = df[['time', 'open', 'high', 'low', 'close']].dropna()

        return df, None

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"Error fetching bars: {error_msg}")
        traceback.print_exc()
        return None, error_msg


def place_order(symbol, side, quantity, order_type='MKT', limit_price=None):
    """Place a market or limit order through IB"""
    try:
        if not ib or not ib.isConnected():
            return False, "Not connected to IB"

        from ib_insync import MarketOrder, LimitOrder

        stock = Stock(symbol, 'SMART', 'USD')

        if order_type == 'MKT':
            order = MarketOrder(side, quantity)
        elif order_type == 'LMT':
            order = LimitOrder(side, quantity, limit_price)

        trade = ib.placeOrder(stock, order)

        return True, f"{side} order placed: {quantity} {symbol} @ market"

    except Exception as e:
        return False, f"Order failed: {e}"


# Initialize Dash app
app = dash.Dash(__name__)

app.layout = html.Div(
    style={
        'fontFamily': "'Courier New', monospace",
        'backgroundColor': '#0a0e27',
        'color': '#e0e0e0',
        'minHeight': '100vh',
        'padding': '20px'
    },
    children=[
        html.Div(
            style={
                'maxWidth': '1400px',
                'margin': '0 auto'
            },
            children=[
                # Header
                html.Div(
                    style={
                        'textAlign': 'center',
                        'marginBottom': '30px',
                        'borderBottom': '2px solid #00d9ff',
                        'paddingBottom': '20px'
                    },
                    children=[
                        html.H1(
                            '⚡ TRADING DASHBOARD',
                            style={
                                'margin': '0',
                                'fontSize': '32px',
                                'color': '#00d9ff',
                                'fontWeight': 'bold',
                                'letterSpacing': '3px'
                            }
                        ),
                        html.P(
                            'Plotly + IB Gateway | Real-time Execution',
                            style={'color': '#888', 'marginTop': '8px', 'fontSize': '12px'}
                        )
                    ]
                ),

                # Connection Status
                html.Div(
                    id='status-display',
                    style={
                        'padding': '10px 15px',
                        'backgroundColor': '#1a1f3a',
                        'borderLeft': '3px solid #ff6b6b',
                        'marginBottom': '20px',
                        'fontSize': '13px'
                    },
                    children='🔄 Connecting to IB Gateway...'
                ),

                # Control Panel
                html.Div(
                    style={
                        'display': 'grid',
                        'gridTemplateColumns': 'repeat(auto-fit, minmax(200px, 1fr))',
                        'gap': '15px',
                        'marginBottom': '25px'
                    },
                    children=[
                        # Symbol input
                        html.Div([
                            html.Label('Symbol', style={'fontSize': '12px', 'color': '#aaa'}),
                            dcc.Input(
                                id='symbol-input',
                                type='text',
                                placeholder='AAPL',
                                value='MSFT',
                                style={
                                    'width': '100%',
                                    'padding': '10px',
                                    'backgroundColor': '#1a1f3a',
                                    'border': '1px solid #333',
                                    'color': '#fff',
                                    'borderRadius': '4px',
                                    'fontFamily': "'Courier New', monospace"
                                }
                            )
                        ]),

                        # Time Interval
                        html.Div([
                            html.Label('Interval', style={'fontSize': '12px', 'color': '#aaa'}),
                            dcc.Dropdown(
                                id='interval-dropdown',
                                options=[
                                    {'label': '1 min', 'value': '1min'},
                                    {'label': '5 min', 'value': '5min'},
                                    {'label': '15 min', 'value': '15min'},
                                    {'label': '1 hour', 'value': '1hour'},
                                    {'label': '1 day', 'value': '1day'},
                                ],
                                value='1min',
                                style={
                                    'backgroundColor': '#1a1f3a',
                                    'border': '1px solid #333',
                                    'color': '#fff'
                                }
                            )
                        ]),

                        # Duration
                        html.Div([
                            html.Label('Duration', style={'fontSize': '12px', 'color': '#aaa'}),
                            dcc.Dropdown(
                                id='duration-dropdown',
                                options=[
                                    {'label': '1 hour', 'value': '60 min'},
                                    {'label': '4 hours', 'value': '240 min'},
                                    {'label': '1 day', 'value': '1 D'},
                                    {'label': '5 days', 'value': '5 D'},
                                ],
                                value='240 min'
                            )
                        ]),

                        # Buttons
                        html.Div(
                            style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'},
                            children=[
                                html.Button(
                                    '🔌 Connect IB',
                                    id='connect-btn',
                                    n_clicks=0,
                                    style={
                                        'flex': '1',
                                        'padding': '10px',
                                        'backgroundColor': '#00d9ff',
                                        'color': '#000',
                                        'border': 'none',
                                        'borderRadius': '4px',
                                        'fontWeight': 'bold',
                                        'cursor': 'pointer',
                                        'fontSize': '12px'
                                    }
                                ),
                                html.Button(
                                    '📊 Load Chart',
                                    id='load-chart-btn',
                                    n_clicks=0,
                                    style={
                                        'flex': '1',
                                        'padding': '10px',
                                        'backgroundColor': '#00d9ff',
                                        'color': '#000',
                                        'border': 'none',
                                        'borderRadius': '4px',
                                        'fontWeight': 'bold',
                                        'cursor': 'pointer',
                                        'fontSize': '12px'
                                    }
                                ),
                            ]
                        ),
                    ]
                ),

                # Chart
                dcc.Graph(
                    id='candlestick-chart',
                    style={'marginBottom': '25px'},
                    config={'responsive': True}
                ),

                # Trading Panel
                html.Div(
                    style={
                        'display': 'grid',
                        'gridTemplateColumns': 'repeat(auto-fit, minmax(250px, 1fr))',
                        'gap': '15px',
                        'marginBottom': '25px'
                    },
                    children=[
                        # Buy Order
                        html.Div(
                            style={
                                'backgroundColor': '#1a2e1a',
                                'border': '1px solid #00aa44',
                                'padding': '15px',
                                'borderRadius': '4px'
                            },
                            children=[
                                html.H3('BUY ORDER',
                                        style={'color': '#00aa44', 'margin': '0 0 15px 0', 'fontSize': '14px'}),
                                html.Div([
                                    html.Label('Quantity', style={'fontSize': '12px'}),
                                    dcc.Input(
                                        id='buy-qty',
                                        type='number',
                                        placeholder='100',
                                        value=100,
                                        style={
                                            'width': '100%',
                                            'padding': '8px',
                                            'marginBottom': '10px',
                                            'backgroundColor': '#0a1f0a',
                                            'border': '1px solid #00aa44',
                                            'color': '#fff',
                                            'borderRadius': '3px'
                                        }
                                    )
                                ]),
                                html.Button(
                                    '🟢 BUY @ MARKET',
                                    id='buy-btn',
                                    n_clicks=0,
                                    style={
                                        'width': '100%',
                                        'padding': '10px',
                                        'backgroundColor': '#00aa44',
                                        'color': '#000',
                                        'border': 'none',
                                        'borderRadius': '3px',
                                        'fontWeight': 'bold',
                                        'cursor': 'pointer',
                                        'fontSize': '13px'
                                    }
                                ),
                                html.Div(id='buy-status',
                                         style={'marginTop': '10px', 'fontSize': '12px', 'color': '#aaa'})
                            ]
                        ),

                        # Sell Order
                        html.Div(
                            style={
                                'backgroundColor': '#2e1a1a',
                                'border': '1px solid #aa0000',
                                'padding': '15px',
                                'borderRadius': '4px'
                            },
                            children=[
                                html.H3('SELL ORDER',
                                        style={'color': '#ff5555', 'margin': '0 0 15px 0', 'fontSize': '14px'}),
                                html.Div([
                                    html.Label('Quantity', style={'fontSize': '12px'}),
                                    dcc.Input(
                                        id='sell-qty',
                                        type='number',
                                        placeholder='100',
                                        value=100,
                                        style={
                                            'width': '100%',
                                            'padding': '8px',
                                            'marginBottom': '10px',
                                            'backgroundColor': '#1f0a0a',
                                            'border': '1px solid #aa0000',
                                            'color': '#fff',
                                            'borderRadius': '3px'
                                        }
                                    )
                                ]),
                                html.Button(
                                    '🔴 SELL @ MARKET',
                                    id='sell-btn',
                                    n_clicks=0,
                                    style={
                                        'width': '100%',
                                        'padding': '10px',
                                        'backgroundColor': '#ff5555',
                                        'color': '#000',
                                        'border': 'none',
                                        'borderRadius': '3px',
                                        'fontWeight': 'bold',
                                        'cursor': 'pointer',
                                        'fontSize': '13px'
                                    }
                                ),
                                html.Div(id='sell-status',
                                         style={'marginTop': '10px', 'fontSize': '12px', 'color': '#aaa'})
                            ]
                        ),
                    ]
                ),

                # Order History / Log
                html.Div(
                    style={
                        'backgroundColor': '#1a1f3a',
                        'border': '1px solid #333',
                        'padding': '15px',
                        'borderRadius': '4px'
                    },
                    children=[
                        html.H3('Order Log', style={'margin': '0 0 10px 0', 'color': '#00d9ff', 'fontSize': '14px'}),
                        html.Div(id='order-log', style={'fontSize': '11px', 'fontFamily': "'Courier New', monospace",
                                                        'maxHeight': '200px', 'overflowY': 'auto'})
                    ]
                )
            ]
        ),

        # Hidden store for data
        dcc.Store(id='chart-data-store'),
        dcc.Store(id='order-log-store', data=[]),
    ]
)


# Callbacks
@app.callback(
    Output('status-display', 'children'),
    Input('connect-btn', 'n_clicks'),
    prevent_initial_call=False
)
def show_ib_status(n_clicks):
    """Display IB connection status (set at startup)"""
    status_icon = '✓' if ib_connected else '✗'
    return f'{status_icon} {ib_status}'


@app.callback(
    [Output('chart-data-store', 'data'),
     Output('status-display', 'children', allow_duplicate=True)],
    Input('load-chart-btn', 'n_clicks'),
    [State('symbol-input', 'value'),
     State('interval-dropdown', 'value'),
     State('duration-dropdown', 'value')],
    prevent_initial_call=True
)
def load_chart_data(n_clicks, symbol, interval, duration):
    df, error = fetch_bars(symbol, duration, interval)

    if error:
        return None, f'✗ Error: {error}'

    return df.to_json(date_format='iso', orient='split'), f'✓ Loaded {len(df)} bars for {symbol}'


@app.callback(
    Output('candlestick-chart', 'figure'),
    Input('chart-data-store', 'data'),
    State('symbol-input', 'value')
)
def update_chart(stored_data, symbol):
    if not stored_data:
        return {'data': [], 'layout': {'title': 'Load a chart first'}}

    df = pd.read_json(stored_data, orient='split')
    df['time'] = pd.to_datetime(df['time'])

    fig = go.Figure(data=[go.Candlestick(
        x=df['time'],
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name=symbol
    )])

    fig.update_layout(
        title=f'{symbol} - Candlestick Chart',
        yaxis_title='Price (USD)',
        xaxis_title='Time',
        template='plotly_dark',
        hovermode='x unified',
        height=600
    )

    return fig


@app.callback(
    [Output('buy-status', 'children'),
     Output('order-log-store', 'data')],
    Input('buy-btn', 'n_clicks'),
    [State('symbol-input', 'value'),
     State('buy-qty', 'value'),
     State('order-log-store', 'data')],
    prevent_initial_call=True
)
def execute_buy(n_clicks, symbol, qty, log_data):
    if not qty or qty <= 0:
        return '❌ Invalid quantity', log_data

    success, message = place_order(symbol, 'BUY', int(qty))
    status = f"{'✓' if success else '✗'} {message}"

    if log_data is None:
        log_data = []
    log_data.append(f"[BUY] {symbol} x{qty} - {message}")
    log_data = log_data[-20:]  # Keep last 20

    return status, log_data


@app.callback(
    [Output('sell-status', 'children'),
     Output('order-log-store', 'data', allow_duplicate=True)],
    Input('sell-btn', 'n_clicks'),
    [State('symbol-input', 'value'),
     State('sell-qty', 'value'),
     State('order-log-store', 'data')],
    prevent_initial_call=True
)
def execute_sell(n_clicks, symbol, qty, log_data):
    if not qty or qty <= 0:
        return '❌ Invalid quantity', log_data

    success, message = place_order(symbol, 'SELL', int(qty))
    status = f"{'✓' if success else '✗'} {message}"

    if log_data is None:
        log_data = []
    log_data.append(f"[SELL] {symbol} x{qty} - {message}")
    log_data = log_data[-20:]  # Keep last 20

    return status, log_data


@app.callback(
    Output('order-log', 'children'),
    Input('order-log-store', 'data')
)
def update_log(log_data):
    if not log_data:
        return "No orders yet"
    return '\n'.join(reversed(log_data))


if __name__ == '__main__':
    print("\n🚀 Starting Trading Dashboard...")
    print("📍 Open: http://127.0.0.1:8050\n")

    try:
        app.run(debug=True, host='127.0.0.1', port=8050)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        if ib and ib.isConnected():
            ib.disconnect()
            print("✓ Disconnected from IB Gateway")
    finally:
        if ib and ib.isConnected():
            ib.disconnect()