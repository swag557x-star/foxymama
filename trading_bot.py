import cbpro
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CoinbaseProBot:
    def __init__(self, api_key, api_secret, passphrase, api_url="https://api.pro.coinbase.com"):
        self.auth_client = cbpro.AuthenticatedClient(api_key, api_secret, passphrase, api_url)

    def get_accounts(self):
        try:
            accounts = self.auth_client.get_accounts()
            return accounts
        except Exception as e:
            logger.error(f"Error fetching accounts: {e}")
            return None

    def get_product_ticker(self, product_id="BTC-USD"):
        try:
            ticker = self.auth_client.get_product_ticker(product_id=product_id)
            return ticker
        except Exception as e:
            logger.error(f"Error fetching ticker for {product_id}: {e}")
            return None

    def place_market_order(self, side, product_id="BTC-USD", funds=None):
        try:
            if side not in ["buy", "sell"]:
                logger.error("Order side must be 'buy' or 'sell'")
                return None
            order = self.auth_client.place_market_order(product_id=product_id, side=side, funds=funds)
            return order
        except Exception as e:
            logger.error(f"Error placing {side} order: {e}")
            return None

def main():
    if len(sys.argv) < 5:
        print("Usage: python trading_bot.py <api_key> <api_secret> <passphrase> <command> [<args>]")
        print("Commands:")
        print("  balance")
        print("  ticker [product_id]")
        print("  buy <funds> [product_id]")
        print("  sell <funds> [product_id]")
        sys.exit(1)

    api_key = sys.argv[1]
    api_secret = sys.argv[2]
    passphrase = sys.argv[3]
    command = sys.argv[4]

    bot = CoinbaseProBot(api_key, api_secret, passphrase)

    if command == "balance":
        accounts = bot.get_accounts()
        if accounts:
            for account in accounts:
                print(f"{account['currency']}: {account['balance']}")
    elif command == "ticker":
        product_id = sys.argv[5] if len(sys.argv) > 5 else "BTC-USD"
        ticker = bot.get_product_ticker(product_id)
        if ticker:
            print(f"Price for {product_id}: {ticker['price']}")
    elif command == "buy":
        if len(sys.argv) < 6:
            print("Usage: buy <funds> [product_id]")
            sys.exit(1)
        funds = sys.argv[5]
        product_id = sys.argv[6] if len(sys.argv) > 6 else "BTC-USD"
        order = bot.place_market_order("buy", product_id, funds)
        if order:
            print(f"Buy order placed: {order}")
    elif command == "sell":
        if len(sys.argv) < 6:
            print("Usage: sell <funds> [product_id]")
            sys.exit(1)
        funds = sys.argv[5]
        product_id = sys.argv[6] if len(sys.argv) > 6 else "BTC-USD"
        order = bot.place_market_order("sell", product_id, funds)
        if order:
            print(f"Sell order placed: {order}")
    else:
        print(f"Unknown command: {command}")

if __name__ == "__main__":
    main()
