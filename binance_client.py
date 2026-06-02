"""
Minimal Binance Spot REST client.

Handles the parts that are easy to get wrong:
  - HMAC-SHA256 signing of authenticated requests
  - timestamp + recvWindow handling
  - sane error messages from Binance's JSON error bodies

Docs: https://developers.binance.com/docs/binance-spot-api-docs
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

LIVE_BASE_URL = "https://api.binance.com"
TESTNET_BASE_URL = "https://testnet.binance.vision"


class BinanceError(Exception):
    """Raised when Binance returns an error response."""

    def __init__(self, code: int, msg: str, status: int):
        self.code = code
        self.msg = msg
        self.status = status
        super().__init__(f"Binance error {code} (HTTP {status}): {msg}")


class BinanceClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool = False,
        recv_window: int = 5000,
    ):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET")
        self.base_url = TESTNET_BASE_URL if testnet else LIVE_BASE_URL
        self.recv_window = recv_window

        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    # ----- internals -------------------------------------------------------

    def _sign(self, params: dict[str, Any]) -> str:
        query = urlencode(params, doseq=True)
        return hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}

        if signed:
            if not self.api_key or not self.api_secret:
                raise RuntimeError(
                    "API key and secret are required for signed requests. "
                    "Set BINANCE_API_KEY and BINANCE_API_SECRET."
                )
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window
            params["signature"] = self._sign(params)

        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, params=params, timeout=10)

        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise

        if not resp.ok or (isinstance(data, dict) and data.get("code", 0) < 0):
            code = data.get("code", -1) if isinstance(data, dict) else -1
            msg = data.get("msg", resp.text) if isinstance(data, dict) else resp.text
            raise BinanceError(code, msg, resp.status_code)

        return data

    # ----- public market data (no key required) ---------------------------

    def ping(self) -> dict:
        """Test connectivity."""
        return self._request("GET", "/api/v3/ping")

    def server_time(self) -> dict:
        return self._request("GET", "/api/v3/time")

    def price(self, symbol: str) -> dict:
        """Latest price for a symbol, e.g. 'BTCUSDT'."""
        return self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})

    def order_book(self, symbol: str, limit: int = 100) -> dict:
        return self._request(
            "GET", "/api/v3/depth", {"symbol": symbol, "limit": limit}
        )

    def exchange_info(self, symbol: str) -> dict:
        return self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})

    def lot_step_size(self, symbol: str) -> float:
        """The quantity increment for a symbol (LOT_SIZE filter), e.g. 0.00001."""
        info = self.exchange_info(symbol)
        for f in info["symbols"][0]["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return float(f["stepSize"])
        return 0.00000001

    def klines(self, symbol: str, interval: str = "1h", limit: int = 100,
               start_time: int | None = None, end_time: int | None = None) -> list:
        """Candlestick data. interval e.g. '1m','5m','1h','1d'.

        start_time / end_time are epoch milliseconds (for paging history).
        """
        return self._request(
            "GET",
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit,
             "startTime": start_time, "endTime": end_time},
        )

    # ----- account (signed) -----------------------------------------------

    def account(self) -> dict:
        """Account info incl. balances. Needs 'Enable Reading' permission."""
        return self._request("GET", "/api/v3/account", signed=True)

    def balances(self, hide_zero: bool = True) -> list[dict]:
        """Convenience: just the non-zero balances."""
        bals = self.account()["balances"]
        if hide_zero:
            bals = [b for b in bals if float(b["free"]) or float(b["locked"])]
        return bals

    def open_orders(self, symbol: str | None = None) -> list:
        return self._request(
            "GET", "/api/v3/openOrders", {"symbol": symbol}, signed=True
        )

    # ----- trading (signed) — needs 'Enable Spot Trading' -----------------

    def create_order(
        self,
        symbol: str,
        side: str,            # 'BUY' or 'SELL'
        type: str,            # 'MARKET', 'LIMIT', ...
        quantity: float | None = None,
        price: float | None = None,
        time_in_force: str | None = None,   # 'GTC' for LIMIT orders
        quote_order_qty: float | None = None,
        **extra: Any,
    ) -> dict:
        params = {
            "symbol": symbol,
            "side": side,
            "type": type,
            "quantity": quantity,
            "price": price,
            "timeInForce": time_in_force,
            "quoteOrderQty": quote_order_qty,
            **extra,
        }
        return self._request("POST", "/api/v3/order", params, signed=True)

    def test_order(self, **kwargs: Any) -> dict:
        """Validate an order without sending it to the matching engine."""
        params = {
            "symbol": kwargs.get("symbol"),
            "side": kwargs.get("side"),
            "type": kwargs.get("type"),
            "quantity": kwargs.get("quantity"),
            "price": kwargs.get("price"),
            "timeInForce": kwargs.get("time_in_force"),
            "quoteOrderQty": kwargs.get("quote_order_qty"),
        }
        return self._request("POST", "/api/v3/order/test", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request(
            "DELETE",
            "/api/v3/order",
            {"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def cancel_all_orders(self, symbol: str) -> list:
        return self._request(
            "DELETE", "/api/v3/openOrders", {"symbol": symbol}, signed=True
        )
