import os
import logging
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from coinbase_advanced_trader import EnhancedRESTClient
import ta
import schedule
import time
from telegram import Bot
import asyncio
import requests
import random

# Set up Telegram bot
# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Get trading mode from environment variables (default to 'demo')
mode = os.getenv('MODE', 'demo').lower()
if mode not in ['demo', 'live']:
    logger.warning(f"Invalid MODE '{mode}', defaulting to 'demo'")
    mode = 'demo'

# Set SAFE_MODE based on mode
SAFE_MODE = (mode == 'demo')

# Get API credentials based on mode
if mode == 'demo':
    api_key = os.getenv('DEMO_COINBASE_API_KEY')
    api_secret_env_var = 'DEMO_COINBASE_API_SECRET'
else:
    api_key = os.getenv('LIVE_COINBASE_API_KEY')
    api_secret_env_var = 'LIVE_COINBASE_API_SECRET'

# Manually parse the API secret from .env file since dotenv has issues with multiline
api_secret = None
try:
    with open('.env', 'r') as f:
        content = f.read()
        lines = content.split('\n')
        secret_lines = []
        in_secret = False
        for line in lines:
            stripped_line = line.strip()
            if stripped_line.startswith(f'{api_secret_env_var}='):
                secret_lines.append(stripped_line.split('=', 1)[1])
                in_secret = True
            elif in_secret and stripped_line.startswith('-----END'):
                secret_lines.append(stripped_line)
                break
            elif in_secret:
                secret_lines.append(stripped_line)
        api_secret = '\n'.join(secret_lines).strip()
except Exception as e:
    logger.error(f"Error reading API secret from .env: {e}")
    api_secret = os.getenv(api_secret_env_var)

# Get Telegram credentials
telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

# Initialize the Coinbase Advanced Trade API client
api = EnhancedRESTClient(api_key=api_key, api_secret=api_secret)

# Initialize Telegram bot if credentials are provided
telegram_bot = None
if telegram_bot_token and telegram_chat_id:
    telegram_bot = Bot(token=telegram_bot_token)
    logger.info("Telegram bot initialized for notifications")
else:
    logger.warning("Telegram credentials not found; notifications disabled")

# Global dictionary to track open positions
positions = {}

# Stop loss percentage: 1% below buy price
STOP_LOSS_PERCENTAGE = 0.01

# Take profit percentage: 2% above entry
TAKE_PROFIT_PERCENTAGE = 0.02

# NewsAPI key (add to .env)
NEWS_API_KEY = os.getenv('NEWS_API_KEY')

async def send_telegram_message(message):
    """Send a message to Telegram chat."""
    if telegram_bot and telegram_chat_id:
        try:
            await telegram_bot.send_message(chat_id=telegram_chat_id, text=message)
            logger.info(f"Sent Telegram message: {message}")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
    else:
        logger.warning("Telegram bot not initialized; skipping message")

def fetch_crypto_news():
    """Fetch recent cryptocurrency news from NewsAPI."""
    if not NEWS_API_KEY:
        logger.warning("NEWS_API_KEY not set, skipping news fetch")
        return []
    try:
        url = f"https://newsapi.org/v2/everything?q=cryptocurrency&apiKey={NEWS_API_KEY}&pageSize=10&sortBy=publishedAt"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        articles = data.get('articles', [])
        logger.info(f"Fetched {len(articles)} news articles")
        return articles
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        return []

def analyze_sentiment(articles):
    """Analyze sentiment of news articles."""
    positive_keywords = ['bullish', 'surge', 'rally', 'breakthrough', 'adoption', 'partnership', 'upgrade', 'growth', 'positive', 'gain', 'profit']
    negative_keywords = ['crash', 'dump', 'bearish', 'hack', 'scam', 'ban', 'regulation', 'decline', 'negative', 'loss', 'fall']
    positive_count = 0
    negative_count = 0
    for article in articles:
        title = article.get('title', '').lower()
        description = article.get('description', '').lower()
        text = title + ' ' + description
        for word in positive_keywords:
            if word in text:
                positive_count += 1
        for word in negative_keywords:
            if word in text:
                negative_count += 1
    logger.info(f"Sentiment analysis: positive={positive_count}, negative={negative_count}")
    if positive_count > negative_count:
        return 'positive'
    elif negative_count > positive_count:
        return 'negative'
    else:
        return 'neutral'

def get_trading_products():
    """Fetch all available trading products from Coinbase."""
    try:
        products_response = api.get_products()
        if products_response is None or not hasattr(products_response, 'products'):
            logger.error("get_products() returned invalid response")
            return []
        products = products_response.products
        # Filter for trading-enabled products
        trading_products = [p for p in products if not getattr(p, 'trading_disabled', False)]
        logger.info(f"Fetched {len(trading_products)} trading products")
        return trading_products
    except Exception as e:
        logger.error(f"Error fetching products: {e}")
        return []

def get_historical_data(product_id, granularity='ONE_HOUR', limit=100):
    """Fetch historical candle data for a product."""
    try:
        # Calculate start and end times for the last 'limit' periods
        if granularity == 'ONE_HOUR':
            granularity_seconds = 3600
        elif granularity == 'ONE_MINUTE':
            granularity_seconds = 60
        else:
            granularity_seconds = 3600  # default to 1 hour

        end_time = int(time.time())
        start_time = end_time - (limit * granularity_seconds)
        candles_response = api.get_public_candles(product_id, start=str(start_time), end=str(end_time), granularity=granularity)
        candles = candles_response.candles
        df = pd.DataFrame([candle.__dict__ for candle in candles])
        df['start'] = pd.to_datetime(df['start'].astype(int), unit='s')
        df.set_index('start', inplace=True)
        df = df.astype(float)
        return df
    except Exception as e:
        logger.error(f"Error fetching historical data for {product_id}: {e}")
        return pd.DataFrame()

def calculate_indicators(df):
    """Calculate technical indicators."""
    if df.empty:
        return df

    # RSI
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()

    # MACD
    macd = ta.trend.MACD(df['close'])
    df['macd'] = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_hist'] = macd.macd_diff()

    # EMA
    df['ema20'] = ta.trend.EMAIndicator(df['close'], window=20).ema_indicator()
    df['ema50'] = ta.trend.EMAIndicator(df['close'], window=50).ema_indicator()

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_middle'] = bb.bollinger_mavg()

    # Stochastic Oscillator
    stoch = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'], window=14, smooth_window=3)
    df['stoch_k'] = stoch.stoch()
    df['stoch_d'] = stoch.stoch_signal()

    # OBV (On Balance Volume)
    df['obv'] = ta.volume.OnBalanceVolumeIndicator(df['close'], df['volume']).on_balance_volume()

    # ATR for volatility-based sizing
    df['atr'] = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()

    return df

def generate_signals(df):
    """Generate buy/sell signals based on multiple indicators for higher quality."""
    if df.empty or len(df) < 50:
        return 'HOLD'

    latest = df.iloc[-1]

    # Enhanced Buy signal: Multiple confirmations
    buy_conditions = [
        latest['rsi'] < 30,  # Oversold
        latest['macd_hist'] > 0,  # MACD positive
        latest['ema20'] > latest['ema50'],  # Trend up
        latest['close'] <= latest['bb_lower'],  # Near lower Bollinger Band
        latest['stoch_k'] < 20 and latest['stoch_d'] < 20,  # Oversold stochastic
        latest['obv'] > df['obv'].iloc[-2]  # Volume increasing
    ]

    # Enhanced Sell signal: Multiple confirmations
    sell_conditions = [
        latest['rsi'] > 70,  # Overbought
        latest['macd_hist'] < 0,  # MACD negative
        latest['ema20'] < latest['ema50'],  # Trend down
        latest['close'] >= latest['bb_upper'],  # Near upper Bollinger Band
        latest['stoch_k'] > 80 and latest['stoch_d'] > 80,  # Overbought stochastic
        latest['obv'] < df['obv'].iloc[-2]  # Volume decreasing
    ]

    if sum(buy_conditions) >= 4:  # At least 4 out of 6 conditions
        return 'BUY'
    elif sum(sell_conditions) >= 4:  # At least 4 out of 6 conditions
        return 'SELL'

    return 'HOLD'

def get_account_balance():
    """Get total account balance in USDC or USD."""
    try:
        accounts_response = api.get_accounts()
        # Handle different response formats
        if hasattr(accounts_response, 'accounts'):
            accounts = accounts_response.accounts
        elif isinstance(accounts_response, dict) and 'accounts' in accounts_response:
            accounts = accounts_response['accounts']
        else:
            accounts = accounts_response if isinstance(accounts_response, list) else []

        logger.info("All account balances:")
        usdc_balance = 0
        usd_balance = 0
        for account in accounts:
            if hasattr(account, 'currency'):
                currency = account.currency
            elif isinstance(account, dict):
                currency = account.get('currency')
            else:
                continue

            balance = 0
            if hasattr(account, 'available_balance') and hasattr(account.available_balance, 'value'):
                balance = float(account.available_balance.value)
            elif isinstance(account, dict) and 'available_balance' in account:
                balance_info = account['available_balance']
                if isinstance(balance_info, dict) and 'value' in balance_info:
                    balance = float(balance_info['value'])

            logger.info(f"  {currency}: ${balance:.2f}")

            if currency == 'USDC':
                usdc_balance = balance
            elif currency == 'USD':
                usd_balance = balance

        # Prefer USDC, but fall back to USD if USDC is zero
        if usdc_balance > 0:
            return usdc_balance
        elif usd_balance > 0:
            logger.info(f"No USDC balance found, using USD balance: ${usd_balance:.2f}")
            return usd_balance
        else:
            return 0
    except Exception as e:
        logger.error(f"Error getting account balance: {e}")
        return 0

def view_orders():
    """View current open orders and positions."""
    logger.info("Viewing current orders and positions...")

    # Display open positions
    if positions:
        logger.info("Open Positions:")
        for product_id, pos in positions.items():
            position_type = "short" if pos.get('is_short', False) else "long"
            entry_price = pos['entry_price']
            size = pos['size']
            logger.info(f"  {product_id}: {position_type.upper()} - Size: {size:.6f}, Entry: ${entry_price:.2f}")
    else:
        logger.info("No open positions.")

    # Fetch and display open orders from Coinbase
    try:
        # Try different methods to get orders
        try:
            orders_response = api.get_orders()
            if orders_response and hasattr(orders_response, 'orders'):
                open_orders = [order for order in orders_response.orders if order.get('status') == 'OPEN']
            else:
                open_orders = []
        except AttributeError:
            # If get_orders doesn't exist, try list_orders
            try:
                orders_response = api.list_orders()
                if orders_response and hasattr(orders_response, 'orders'):
                    open_orders = [order for order in orders_response.orders if order.get('status') == 'OPEN']
                else:
                    open_orders = []
            except AttributeError:
                logger.info("Order fetching methods not available in API client.")
                open_orders = []

        if open_orders:
            logger.info("Open Orders:")
            for order in open_orders:
                product_id = order.get('product_id', 'Unknown')
                side = order.get('side', 'Unknown')
                size = float(order.get('size', 0))
                price = float(order.get('price', 0))
                logger.info(f"  {product_id}: {side.upper()} - Size: {size:.6f}, Price: ${price:.2f}")
        else:
            logger.info("No open orders.")
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")

    # Display account balance
    balance = get_account_balance()
    logger.info(f"USDC Balance: ${balance:.2f} USDC")

def execute_trade(product_id, side, size, price):
    """Execute a trade with risk management."""
    if SAFE_MODE:
        logger.info(f"[SAFE MODE] Would place {side} order for {product_id}: size={size}, price={price}")
        asyncio.run(send_telegram_message(f"[SAFE MODE] {side.upper()} signal for {product_id}: size={size:.6f}, price={price:.2f}"))
        return True  # Simulate success
    try:
        # For simplicity, place a limit order
        order = api.create_order(
            product_id=product_id,
            side=side,
            order_configuration={
                'limit_limit_gtc': {
                    'base_size': str(size),
                    'limit_price': str(price),
                    'post_only': False
                }
            }
        )
        logger.info(f"Placed {side} order for {product_id}: {order}")
        asyncio.run(send_telegram_message(f"Executed {side.upper()} order for {product_id}: size={size:.6f}, price={price:.2f}"))
        return order
    except Exception as e:
        logger.error(f"Error executing trade: {e}")
        asyncio.run(send_telegram_message(f"Error executing {side.upper()} trade for {product_id}: {e}"))
        return None

def record_position(product_id, size, price, is_short=False):
    """Record a new position after a buy or sell."""
    # Add randomness to stop loss and take profit
    stop_loss_pct = STOP_LOSS_PERCENTAGE + random.uniform(-0.0005, 0.0005)  # ±0.05%
    take_profit_pct = TAKE_PROFIT_PERCENTAGE + random.uniform(-0.001, 0.001)  # ±0.1%

    positions[product_id] = {
        'size': size,
        'entry_price': price,
        'is_short': is_short,
        'timestamp': time.time(),
        'stop_loss_pct': stop_loss_pct,
        'take_profit_pct': take_profit_pct,
        'highest_price': price if not is_short else float('inf'),  # For trailing stop
        'lowest_price': price if is_short else float('-inf')  # For trailing stop
    }
    position_type = "short" if is_short else "long"
    logger.info(f"Recorded {position_type} position for {product_id}: size={size}, entry_price={price}, stop_loss={stop_loss_pct:.4f}, take_profit={take_profit_pct:.4f}")

def calculate_pl(product_id, exit_price):
    """Calculate and log P/L for a closed position."""
    if product_id not in positions:
        logger.warning(f"No position found for {product_id} to calculate P/L")
        return
    pos = positions[product_id]
    entry_price = pos['entry_price']
    size = pos['size']
    is_short = pos.get('is_short', False)

    if is_short:
        # For short positions, profit when price goes down
        pl = (entry_price - exit_price) * size
        logger.info(f"P/L for {product_id} (short): Entry@{entry_price:.2f}, Exit@{exit_price:.2f}, Size={size:.6f}, P/L={pl:.2f} USD")
        asyncio.run(send_telegram_message(f"P/L for {product_id} (short): Entry@{entry_price:.2f}, Exit@{exit_price:.2f}, Size={size:.6f}, P/L={pl:.2f} USD"))
    else:
        # For long positions, profit when price goes up
        pl = (exit_price - entry_price) * size
        logger.info(f"P/L for {product_id} (long): Entry@{entry_price:.2f}, Exit@{exit_price:.2f}, Size={size:.6f}, P/L={pl:.2f} USD")
        asyncio.run(send_telegram_message(f"P/L for {product_id} (long): Entry@{entry_price:.2f}, Exit@{exit_price:.2f}, Size={size:.6f}, P/L={pl:.2f} USD"))

    del positions[product_id]

def check_stop_loss():
    """Check and execute stop loss and take profit orders for open positions."""
    for product_id, pos in list(positions.items()):
        try:
            # Get current price
            df = get_historical_data(product_id, granularity='ONE_MINUTE', limit=1)  # Get latest 1-minute candle
            if df.empty:
                continue
            current_price = df.iloc[-1]['close']
            entry_price = pos['entry_price']
            is_short = pos.get('is_short', False)
            stop_loss_pct = pos.get('stop_loss_pct', STOP_LOSS_PERCENTAGE)
            take_profit_pct = pos.get('take_profit_pct', TAKE_PROFIT_PERCENTAGE)

            # Update highest/lowest prices for trailing stop
            if is_short:
                pos['lowest_price'] = min(pos['lowest_price'], current_price)
                # Trailing stop for short: adjust stop loss if price drops further
                trailing_stop_price = pos['lowest_price'] * (1 + stop_loss_pct)
                if trailing_stop_price < entry_price * (1 + stop_loss_pct):
                    pos['stop_loss_pct'] = (trailing_stop_price / entry_price) - 1
            else:
                pos['highest_price'] = max(pos['highest_price'], current_price)
                # Trailing stop for long: adjust stop loss if price rises further
                trailing_stop_price = pos['highest_price'] * (1 - stop_loss_pct)
                if trailing_stop_price > entry_price * (1 - stop_loss_pct):
                    pos['stop_loss_pct'] = 1 - (trailing_stop_price / entry_price)

            # Check take profit first
            if is_short:
                # For short positions, take profit when price drops enough
                take_profit_price = entry_price * (1 - take_profit_pct)
                if current_price <= take_profit_price:
                    logger.info(f"Take profit triggered for {product_id} (short): Current@{current_price:.2f}, Target@{take_profit_price:.2f}")
                    asyncio.run(send_telegram_message(f"Take profit triggered for {product_id} (short): Current@{current_price:.2f}, Target@{take_profit_price:.2f}"))

                    # Execute buy order to close short position
                    size = pos['size']
                    order = execute_trade(product_id, 'buy', size, current_price)
                    if order:
                        calculate_pl(product_id, current_price)
                    continue
            else:
                # For long positions, take profit when price rises enough
                take_profit_price = entry_price * (1 + take_profit_pct)
                if current_price >= take_profit_price:
                    logger.info(f"Take profit triggered for {product_id} (long): Current@{current_price:.2f}, Target@{take_profit_price:.2f}")
                    asyncio.run(send_telegram_message(f"Take profit triggered for {product_id} (long): Current@{current_price:.2f}, Target@{take_profit_price:.2f}"))

                    # Execute sell order to close long position
                    size = pos['size']
                    order = execute_trade(product_id, 'sell', size, current_price)
                    if order:
                        calculate_pl(product_id, current_price)
                    continue

            # Check stop loss with trailing adjustment
            if is_short:
                # For short positions, stop loss is above entry price (use position-specific)
                stop_loss_price = entry_price * (1 + stop_loss_pct)
                if current_price >= stop_loss_price:
                    logger.info(f"Stop loss triggered for {product_id} (short): Current@{current_price:.2f}, Stop@{stop_loss_price:.2f}")
                    asyncio.run(send_telegram_message(f"Stop loss triggered for {product_id} (short): Current@{current_price:.2f}, Stop@{stop_loss_price:.2f}"))

                    # Execute buy order to close short position
                    size = pos['size']
                    order = execute_trade(product_id, 'buy', size, current_price)
                    if order:
                        calculate_pl(product_id, current_price)
            else:
                # For long positions, stop loss is below entry price (use position-specific)
                stop_loss_price = entry_price * (1 - stop_loss_pct)
                if current_price <= stop_loss_price:
                    logger.info(f"Stop loss triggered for {product_id} (long): Current@{current_price:.2f}, Stop@{stop_loss_price:.2f}")
                    asyncio.run(send_telegram_message(f"Stop loss triggered for {product_id} (long): Current@{current_price:.2f}, Stop@{stop_loss_price:.2f}"))

                    # Execute sell order to close long position
                    size = pos['size']
                    order = execute_trade(product_id, 'sell', size, current_price)
                    if order:
                        calculate_pl(product_id, current_price)
        except Exception as e:
            logger.error(f"Error checking stop loss for {product_id}: {e}")

def trading_bot():
    """Main trading bot function."""
    logger.info("Starting trading analysis...")

    # Fetch and analyze news sentiment
    articles = fetch_crypto_news()
    sentiment = analyze_sentiment(articles)
    logger.info(f"Overall market sentiment: {sentiment}")

    # Only proceed with trading if sentiment is positive or neutral
    if sentiment == 'negative':
        logger.info("Negative sentiment detected, skipping trading cycle")
        return

    # Check account balance
    balance = get_account_balance()
    logger.info(f"USDC Balance: ${balance:.2f} USDC")

    # Check stop losses first
    check_stop_loss()

    products = get_trading_products()
    if not products:
        return

    # Sort products by volume (assuming volume is available in product data)
    # For simplicity, we'll use the first 5 products as high-volume (BTC, ETH, etc.)
    high_volume_products = products[:5]  # Top 5 products

    position_size = 2.0  # Base $2 position size per trade

    for product in high_volume_products:
        product_id = product['product_id']
        if not product_id.endswith('-USD'):
            continue  # Only USD pairs

        df = get_historical_data(product_id)
        if df.empty:
            continue

        df = calculate_indicators(df)

        # Implement volatility-based sizing
        if 'atr' in df.columns and not df['atr'].empty:
            avg_atr = df['atr'].mean()
            current_atr = df['atr'].iloc[-1]
            # Adjust position size based on current volatility relative to average
            volatility_multiplier = current_atr / avg_atr if avg_atr > 0 else 1.0
            adjusted_position_size = position_size * volatility_multiplier
            # Cap the adjustment to prevent excessive sizing
            adjusted_position_size = min(adjusted_position_size, position_size * 2.0)
            adjusted_position_size = max(adjusted_position_size, position_size * 0.5)
        else:
            adjusted_position_size = position_size

        signal = generate_signals(df)

        if signal == 'BUY':
            # Calculate size based on current price
            current_price = df.iloc[-1]['close']
            size = adjusted_position_size / current_price
            # Place buy order slightly above current price for limit
            buy_price = current_price * 1.001
            order = execute_trade(product_id, 'buy', size, buy_price)
            if order:
                record_position(product_id, size, buy_price)

        elif signal == 'SELL':
            if product_id in positions:
                # Place sell order slightly below current price for limit
                current_price = df.iloc[-1]['close']
                sell_price = current_price * 0.999
                size = positions[product_id]['size']
                order = execute_trade(product_id, 'sell', size, sell_price)
                if order:
                    calculate_pl(product_id, sell_price)
            else:
                logger.info(f"Sell signal for {product_id}, but no open position")

    logger.info("Trading analysis complete")

# Schedule the bot to run every 20 minutes
schedule.every(20).minutes.do(trading_bot)

if __name__ == '__main__':
    logger.info("Coinbase Trading Bot started")
    trading_bot()  # Run once immediately
    while True:
        schedule.run_pending()
        time.sleep(60)
