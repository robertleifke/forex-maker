#!/usr/bin/env python3
"""Recover a half-open CEX-DEX arb by reversing the DEX buy leg, without the engine.

Use when the engine that owns the half-open record cannot run the normal
/recover flow (e.g. the Telegram command keeps landing on another instance).
Reverses the buy using the persisted buy_amount_cngn — never a wallet query —
then closes the attempt and appends the executed history event, mirroring
engine/arb/execution/recovery.py.

Dry run by default; pass --execute to send the transaction.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.config import settings
from engine.accounts import AccountManager, AccountRole
from engine.venues.dex.uniswap_base import UniswapBaseV4Adapter
from engine.venues.dex.uniswap_bsc import UniswapBscV4Adapter

_DEX_VENUES = {"uni-base", "uni-bsc"}


def _build_adapter(venue: str):
    account_manager = AccountManager(
        mnemonic=settings.wallet_mnemonic if settings.wallet_mnemonic else None,
        use_test_accounts=settings.use_test_accounts,
    )
    if venue == "uni-base":
        return UniswapBaseV4Adapter(
            lp_private_key=account_manager.get_private_key(AccountRole.UNI_BASE_LP),
            trade_private_key=account_manager.get_private_key(AccountRole.UNI_BASE_TRADE),
            rpc_url=settings.base_rpc_url,
            params=settings.uni_base_lp_params,
        )
    if venue == "uni-bsc":
        return UniswapBscV4Adapter(
            lp_private_key=account_manager.get_private_key(AccountRole.UNI_BSC_LP),
            trade_private_key=account_manager.get_private_key(AccountRole.UNI_BSC_TRADE),
            rpc_url=settings.bsc_rpc_url,
            params=settings.uni_bsc_lp_params,
        )
    raise ValueError(f"Unsupported DEX venue: {venue}")


def _load_attempt(conn: sqlite3.Connection, opp_id: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM arb_attempts WHERE id = ? AND pipeline = 'cex_dex'",
        (opp_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown CEX-DEX arbitrage opportunity: {opp_id}")
    if row["status"] != "half_open":
        raise ValueError(f"Opportunity {opp_id} is not recoverable from status {row['status']}")
    if row["buy_venue"] not in _DEX_VENUES:
        raise ValueError(
            f"Buy venue {row['buy_venue']} is not a DEX — this script only reverses DEX buy legs"
        )
    if not row["buy_amount_cngn"]:
        raise ValueError(
            f"Cannot recover {opp_id}: buy_amount_cngn not recorded "
            f"(check buy tx {row['buy_tx_hash']} manually)"
        )
    return row


def _close_attempt(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    sell_tx_hash: str,
    actual_profit_usd: float | None,
) -> None:
    now_ms = int(time.time() * 1000)
    reason = "Recovered: reversed DEX buy leg (manual script)"
    conn.execute(
        "UPDATE arb_attempts SET status = 'completed', reason = ?, "
        "actual_profit_usd = ?, updated_at_ms = ? WHERE id = ?",
        (reason, actual_profit_usd, now_ms, row["id"]),
    )
    conn.execute(
        """
        INSERT INTO arb_history_events (
            attempt_id, event_type, timestamp_ms, status, reason,
            optimal_size_usd, routed_size_usd, executed_size_usd,
            expected_profit_usd, actual_profit_usd, net_spread_bps,
            buy_tx_hash, sell_tx_hash
        ) VALUES (?, 'executed', ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"], now_ms, reason,
            row["optimal_size_usd"], row["routed_size_usd"], row["executed_size_usd"],
            row["expected_profit_usd"], actual_profit_usd, row["net_spread_bps"],
            row["buy_tx_hash"], sell_tx_hash,
        ),
    )
    conn.commit()


async def _run(opp_id: str, min_out_stable: Decimal, execute: bool) -> None:
    conn = sqlite3.connect(str(ROOT / settings.db_path))
    row = _load_attempt(conn, opp_id)
    amount_cngn = Decimal(str(row["buy_amount_cngn"]))
    venue = row["buy_venue"]

    adapter = _build_adapter(venue)
    balance_raw = adapter.w3.eth.contract(
        address=adapter.w3.to_checksum_address(adapter.cngn_address),
        abi=adapter.token0.abi,
    ).functions.balanceOf(adapter.trade_account.address).call()
    balance_cngn = Decimal(balance_raw) / Decimal(10 ** adapter.cngn_decimals)

    print(f"opp_id={opp_id}")
    print(f"direction={row['direction']}")
    print(f"buy_venue={venue} buy_tx={row['buy_tx_hash']}")
    print(f"trade_address={adapter.trade_account.address}")
    print(f"reverse_amount_cngn={amount_cngn} (persisted)")
    print(f"wallet_cngn_balance={balance_cngn}")
    print(f"min_out_stable={min_out_stable}")

    if amount_cngn > balance_cngn:
        raise ValueError(
            f"Persisted buy amount {amount_cngn} exceeds wallet balance {balance_cngn} — "
            "the cNGN may have moved; do not force this reversal"
        )

    if not execute:
        print("check_only=true — rerun with --execute to send the reversal")
        return

    amount_in_raw = int(amount_cngn * Decimal(10 ** adapter.cngn_decimals))
    min_out_raw = int(min_out_stable * Decimal(10 ** adapter.stable_decimals))
    result = await adapter.swap(adapter.cngn_address, amount_in_raw, min_out_raw)
    print(f"tx_hash={result.hash}")
    print(f"status={result.status}")
    if result.error:
        print(f"error={result.error}")
        raise SystemExit(1)

    _close_attempt(conn, row, result.hash, actual_profit_usd=None)
    print("attempt marked completed; history event recorded")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reverse a half-open CEX-DEX arb buy leg.")
    parser.add_argument("--opp-id", required=True)
    parser.add_argument("--min-out-stable", type=Decimal, default=Decimal("0"))
    parser.add_argument("--execute", action="store_true", help="Actually send the transaction")
    args = parser.parse_args()
    asyncio.run(_run(args.opp_id, args.min_out_stable, args.execute))


if __name__ == "__main__":
    main()
