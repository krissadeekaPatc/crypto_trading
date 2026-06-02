"""
Run this first to confirm everything works:

    python check_connection.py

It checks public connectivity, then (if keys are set) reads your account.
It does NOT place any orders.
"""

from dotenv import load_dotenv
import os

from binance_client import BinanceClient, BinanceError

load_dotenv()


def main() -> None:
    testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    client = BinanceClient(testnet=testnet)

    print(f"Environment: {'TESTNET' if testnet else 'LIVE'}  ({client.base_url})\n")

    # 1. Public connectivity — no keys needed.
    client.ping()
    print("✓ Connectivity OK")
    print(f"✓ BTCUSDT price: {client.price('BTCUSDT')['price']}")

    # 2. Authenticated read — needs API key with 'Enable Reading'.
    if not client.api_key:
        print("\n(no API key set — skipping account check)")
        print("Copy .env.example to .env and add your keys to test account access.")
        return

    try:
        acct = client.account()
        print(f"\n✓ Authenticated. canTrade={acct['canTrade']}")
        print("Non-zero balances:")
        for b in client.balances():
            print(f"   {b['asset']:>8}  free={b['free']}  locked={b['locked']}")
    except BinanceError as e:
        print(f"\n✗ Account check failed: {e}")
        if e.code == -2015:
            print("  -> Invalid key, IP not whitelisted, or permissions missing.")
        elif e.code == -1021:
            print("  -> Local clock is out of sync with Binance. Sync your system time.")


if __name__ == "__main__":
    main()
