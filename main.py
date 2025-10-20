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
            if line.startswith(f'{api_secret_env_var}='):
                secret_lines.append(line.split('=', 1)[1])
                in_secret = True
            elif in_secret and line.startswith('-----END'):
                secret_lines.append(line)
                break
            elif in_secret:
                secret_lines.append(line)
        api_secret = '\n'.join(secret_lines)
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

# Stop loss percentage: 2% below buy price
STOP_LOSS_PERCENTAGE = 0.02

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
    
    return df

def generate_signals(df):
    """Generate buy/sell signals based on indicators."""
    if df.empty or len(df) < 50:
        return 'HOLD'
    
    latest = df.iloc[-1]
    
    # Buy signal: RSI < 30, MACD hist > 0, EMA20 > EMA50
    if latest['rsi'] < 30 and latest['macd_hist'] > 0 and latest['ema20'] > latest['ema50']:
        return 'BUY'
    
    # Sell signal: RSI > 70 or MACD hist < 0 or EMA20 < EMA50
    if latest['rsi'] > 70 or latest['macd_hist'] < 0 or latest['ema20'] < latest['ema50']:
        return 'SELL'
    
    return 'HOLD'

def get_account_balance():
    """Get total account balance in USD."""
    try:
        accounts = api.get_accounts()
        total_balance = 0
        for account in accounts['accounts']:
            if account['currency'] == 'USDC':
                total_balance = float(account['available_balance']['value'])
                break
        return total_balance
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
    logger.info(f"Account Balance: ${balance:.2f} USD")

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
    positions[product_id] = {
        'size': size,
        'entry_price': price,
        'is_short': is_short,
        'timestamp': time.time()
    }
    position_type = "short" if is_short else "long"
    logger.info(f"Recorded {position_type} position for {product_id}: size={size}, entry_price={price}")

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
    """Check and execute stop loss orders for open positions."""
    for product_id, pos in list(positions.items()):
        try:
            # Get current price
            df = get_historical_data(product_id, granularity='ONE_MINUTE', limit=1)  # Get latest 1-minute candle
            if df.empty:
                continue
            current_price = df.iloc[-1]['close']
            entry_price = pos['entry_price']
            is_short = pos.get('is_short', False)

            if is_short:
                # For short positions, stop loss is above entry price
                stop_loss_price = entry_price * (1 + STOP_LOSS_PERCENTAGE)
                if current_price >= stop_loss_price:
                    logger.info(f"Stop loss triggered for {product_id} (short): Current@{current_price:.2f}, Stop@{stop_loss_price:.2f}")
                    asyncio.run(send_telegram_message(f"Stop loss triggered for {product_id} (short): Current@{current_price:.2f}, Stop@{stop_loss_price:.2f}"))

                    # Execute buy order to close short position
                    size = pos['size']
                    order = execute_trade(product_id, 'buy', size, current_price)
                    if order:
                        calculate_pl(product_id, current_price)
            else:
                # For long positions, stop loss is below entry price
                stop_loss_price = entry_price * (1 - STOP_LOSS_PERCENTAGE)
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

    # Check stop losses first
    check_stop_loss()

    products = get_trading_products()
    if not products:
        return

    position_size = 2.0  # Fixed $2 position size per trade

    for product in products[:10]:  # Limit to first 10 for safety
        product_id = product['product_id']
        if not product_id.endswith('-USD'):
            continue  # Only USD pairs

        df = get_historical_data(product_id)
        if df.empty:
            continue

        df = calculate_indicators(df)
        signal = generate_signals(df)

        if signal == 'BUY':
            # Calculate size based on current price
            current_price = df.iloc[-1]['close']
            size = position_size / current_price
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
