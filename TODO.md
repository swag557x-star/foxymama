# TODO: Coinbase Trading Bot Development

## Setup Phase
- [x] Install python-dotenv for environment variable management
- [x] Create .env file with placeholders for API_KEY and API_SECRET
- [x] Update main.py to load environment variables and initialize Coinbase Advanced Trade API client
- [x] Test the script to ensure API authentication works

## Development Phase
- [x] Install additional dependencies (pandas, numpy, ta, schedule)
- [x] Update main.py: Add imports for new libraries (pandas, numpy, ta, schedule, logging)
- [x] Update main.py: Add function to fetch all trading products from Coinbase
- [x] Update main.py: Implement historical data fetching for each product (1-hour candles, last 100 periods)
- [x] Update main.py: Calculate technical indicators (RSI, MACD, EMA20, EMA50)
- [x] Update main.py: Develop signal generation logic (buy/sell based on indicators)
- [x] Update main.py: Add trade execution with risk management (1% position size, stop-loss at 2%)
- [x] Update main.py: Implement scheduler to run analysis every hour
- [x] Update main.py: Add logging for trades and errors

## Testing and Deployment Phase
- [ ] Test the bot in safe mode (log signals without executing trades)
- [ ] Run the script and monitor logs for issues
- [ ] Refine strategy based on testing results

## Chat Integration Phase
- [x] Install python-telegram-bot library
- [x] Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env file
- [x] Update main.py: Add Telegram bot initialization and message sending function
- [x] Update main.py: Integrate Telegram notifications for trade executions, P/L calculations, and safe mode signals
- [ ] Test Telegram notifications in safe mode
- [ ] Enable live trading with Telegram notifications
