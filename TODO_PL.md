# TODO: Implement P/L Tracking for Trades on All Assets with $2 Position Size

## Implementation Steps
- [x] Change position_size to fixed $2.0 instead of 1% of balance
- [x] Add global positions dictionary to track open positions (product_id: {'size': size, 'buy_price': price, 'timestamp': ts})
- [x] Update execute_trade function to handle both buy and sell orders, and for sells, calculate and log P/L
- [x] Modify trading_bot function: for BUY signals, execute buy and record position; for SELL signals, if position exists, execute sell, calculate P/L, log it, and remove position
- [x] Use limit orders for sells (similar to buys, e.g., slightly below current price)
- [x] Fix API integration issues (use get_public_candles, correct DataFrame creation, fix timestamp parsing)
- [x] Test the updated bot in safe mode (log without executing trades) and monitor logs
- [x] Enable live trading mode (SAFE_MODE = False) and test with real account connection
