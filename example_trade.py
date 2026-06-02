"""
Example: place and cancel orders.  *** LIVE FUNDS — read before running. ***

By default this script only runs test_order(), which validates an order
WITHOUT sending it to the matching engine (no money moves).

To place a REAL order, set DRY_RUN = False below. Start tiny.

    python example_trade.py
"""

from dotenv import load_dotenv
import os

from binance_client import BinanceClient, BinanceError

load_dotenv()

DRY_RUN = True          # True = validate only, no real order is placed.
SYMBOL = "BTCUSDT"


def main() -> None:
    testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    client = BinanceClient(testnet=testnet)
    print(f"Environment: {'TESTNET' if testnet else 'LIVE'}\n")

    # A LIMIT buy well below market so it rests on the book instead of filling.
    price = float(client.price(SYMBOL)["price"])
    limit_price = round(price * 0.90, 2)   # 10% below market

    order_args = dict(
        symbol=SYMBOL,
        side="BUY",
        type="LIMIT",
        quantity=0.0001,                   # tiny size — adjust to your needs
        price=limit_price,
        time_in_force="GTC",
    )
    print(f"Order: BUY 0.0001 {SYMBOL} @ {limit_price} (market {price})")

    try:
        if DRY_RUN:
            client.test_order(**order_args)
            print("✓ test_order passed — order is valid (nothing was placed).")
            print("  Set DRY_RUN=False to place it for real.")
            return

        order = client.create_order(**order_args)
        order_id = order["orderId"]
        print(f"✓ Order placed. orderId={order_id}, status={order['status']}")

        # ... your logic here ...

        client.cancel_order(SYMBOL, order_id)
        print(f"✓ Order {order_id} cancelled.")

    except BinanceError as e:
        print(f"✗ Order failed: {e}")


if __name__ == "__main__":
    main()
