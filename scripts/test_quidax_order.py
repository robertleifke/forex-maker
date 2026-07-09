"""
Test script: place a small buy and sell on Quidax via the engine's QuidaxAdapter.

Usage:
    python -m scripts.test_quidax_order          # dry run — order book only
    python -m scripts.test_quidax_order --live    # place real $1 test orders

Credentials are loaded from .env — never commit keys.
"""

import asyncio
import argparse
from decimal import Decimal
import os
from unittest.mock import AsyncMock, MagicMock

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["QUIDAX_API_KEY"]
USER_ID = os.environ.get("QUIDAX_USER_ID", "me")


def make_adapter():
    from engine.venues.cex.quidax import QuidaxAdapter

    alert_store = MagicMock()
    alert_store.create_alert = AsyncMock()
    state_store = MagicMock()
    state_store.get_system_state = AsyncMock(return_value={})

    return QuidaxAdapter(
        market="usdtcngn",
        api_key=API_KEY,
        order_user_id=USER_ID,
        alert_store=alert_store,
        system_state_store=state_store,
    )


async def fetch_order_book(adapter) -> dict:
    return await adapter._api.get_order_book_payload(limit=50)


def analyze_order_book(data: dict):
    depth = data.get("data", {})
    bids = depth.get("bids", [])
    asks = depth.get("asks", [])

    total_bid_usdt = sum(Decimal(str(v)) for _, v in bids)
    total_ask_usdt = sum(Decimal(str(v)) for _, v in asks)

    print("\n=== ORDER BOOK ===")
    print(f"Market: usdtcngn (base=USDT, quote=cNGN)")
    print(f"BID depth: ${total_bid_usdt:,.2f} USDT ({len(bids)} levels)")
    print(f"ASK depth: ${total_ask_usdt:,.2f} USDT ({len(asks)} levels)")

    best_bid = Decimal(str(bids[0][0])) if bids else Decimal("1397")
    best_ask = Decimal(str(asks[0][0])) if asks else Decimal("1400")
    print(f"Best bid: {best_bid} cNGN/USDT")
    print(f"Best ask: {best_ask} cNGN/USDT")
    return best_bid, best_ask, total_bid_usdt, total_ask_usdt


async def main(live: bool):
    adapter = make_adapter()

    print("Fetching order book...")
    ob = await fetch_order_book(adapter)
    best_bid, best_ask, bid_depth, ask_depth = analyze_order_book(ob)

    if live:
        # Volume denomination is per side (verified against live fills 2026-07-09):
        # sell → USDT to sell (base); buy → cNGN to spend (quote).
        buy_cngn = (best_ask * Decimal("1")).quantize(Decimal("0.01"))  # ~1 USDT worth of cNGN

        print(f"\n=== LIVE TEST ===")
        print(f"Placing SELL — volume=1 (1 USDT, base)...")
        success, exec_usdt, avg_price, error = await adapter.place_market_order("sell", Decimal("1"))
        print(f"  success={success}, exec_usdt={exec_usdt}, avg_price={avg_price}, error={error}")

        print(f"\nPlacing BUY — volume={buy_cngn} (cNGN to spend, quote)...")
        success2, exec_usdt2, avg_price2, error2 = await adapter.place_market_order("buy", buy_cngn)
        print(f"  success={success2}, exec_usdt={exec_usdt2}, avg_price={avg_price2}, error={error2}")
    else:
        print("\n(Dry run — pass --live to place real test orders)")

    await adapter.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.live))
