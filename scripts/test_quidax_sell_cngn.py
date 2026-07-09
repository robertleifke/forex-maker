"""Sell cNGN on Quidax via a market USDT buy, mirroring the engine's CEX sell leg.

Quidax denominates a market-buy ``volume`` in the quote asset — the cNGN to
spend (verified against live fills 2026-07-09) — so the cNGN amount passes
through directly. The ask-book walk is shown for the expected USDT out only.

Usage:
    python -m scripts.test_quidax_sell_cngn --amount-cngn 2800            # dry run
    python -m scripts.test_quidax_sell_cngn --amount-cngn 2800 --execute  # real order
"""

import argparse
import asyncio
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from engine.arb.detection.cex_dex import QUIDAX_FEE, walk_orderbook_asks


def make_adapter():
    from engine.venues.cex.quidax import QuidaxAdapter

    alert_store = MagicMock()
    alert_store.create_alert = AsyncMock()
    state_store = MagicMock()
    state_store.get_system_state = AsyncMock(return_value={})

    return QuidaxAdapter(
        market="usdtcngn",
        api_key=os.environ["QUIDAX_API_KEY"],
        order_user_id=os.environ.get("QUIDAX_USER_ID", "me"),
        alert_store=alert_store,
        system_state_store=state_store,
    )


async def main(amount_cngn: Decimal, execute: bool) -> None:
    adapter = make_adapter()
    try:
        depth = await adapter.get_order_book_depth()
        if depth and depth.asks:
            print(f"asks: {len(depth.asks)} levels, best {depth.asks[0].price} cNGN/USDT")
            expected_usdt, trace = walk_orderbook_asks(depth.asks, amount_cngn, QUIDAX_FEE)
            print(f"spending {amount_cngn} cNGN -> expected ~{expected_usdt:.4f} USDT (fee {QUIDAX_FEE})")
            for t in trace:
                print(f"  level price {t['price']:,.1f} cNGN/USDT, {t['amount']:.4f} USDT")
        else:
            print("warning: no ask depth visible")

        if not execute:
            print("\ncheck_only=true — rerun with --execute to place the order")
            return

        success, executed_usdt, avg_price, error = await adapter.place_market_order("buy", amount_cngn)
        print(f"\nsuccess={success}")
        print(f"executed_usdt={executed_usdt}")
        print(f"avg_price_cngn_per_usdt={avg_price}")
        print(f"error={error}")
    finally:
        await adapter.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sell cNGN on Quidax (market USDT buy).")
    parser.add_argument("--amount-cngn", type=Decimal, required=True)
    parser.add_argument("--execute", action="store_true", help="Actually place the market order")
    args = parser.parse_args()
    asyncio.run(main(args.amount_cngn, args.execute))
