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

# Get API credentials from environment variables
api_key = os.getenv('COINBASE_API_KEY')
api_secret = os.getenv('COINBASE_API_SECRET')

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

# Safe mode: set to True to log signals without executing trades
SAFE_MODE = False

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

def get_historical_data(product_id, granularity=3600, limit=100):
    """Fetch historical candle data for a product."""
    try:
        # Calculate start and end times for the last 'limit' periods
        end_time = int(time.time())
        start_time = end_time - (limit * granularity)
        candles_response = api.get_public_candles(product_id, start=str(start_time), end=str(end_time), granularity='ONE_HOUR')
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
        for account in accounts:
            if account['currency'] == 'USD':
                total_balance = float(account['available_balance']['value'])
                break
        return total_balance
    except Exception as e:
        logger.error(f"Error getting account balance: {e}")
        return 0

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

def record_position(product_id, size, buy_price):
    """Record a new position after a buy."""
    positions[product_id] = {
        'size': size,
        'buy_price': buy_price,
        'timestamp': time.time()
    }
    logger.info(f"Recorded position for {product_id}: size={size}, buy_price={buy_price}")

def calculate_pl(product_id, sell_price):
    """Calculate and log P/L for a closed position."""
    if product_id not in positions:
        logger.warning(f"No position found for {product_id} to calculate P/L")
        return
    pos = positions[product_id]
    buy_price = pos['buy_price']
    size = pos['size']
    pl = (sell_price - buy_price) * size
    logger.info(f"P/L for {product_id}: Buy@{buy_price}, Sell@{sell_price}, Size={size}, P/L={pl:.2f} USD")
    asyncio.run(send_telegram_message(f"P/L for {product_id}: Buy@{buy_price:.2f}, Sell@{sell_price:.2f}, Size={size:.6f}, P/L={pl:.2f} USD"))
    del positions[product_id]

def trading_bot():
    """Main trading bot function."""
    logger.info("Starting trading analysis...")
    
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

# Schedule the bot to run every hour
schedule.every().hour.do(trading_bot)

if __name__ == '__main__':
    logger.info("Coinbase Trading Bot started")
    trading_bot()  # Run once immediately
    while True:
        schedule.run_pending()
        time.sleep(60)
