"""Recovery flows for half-open arbitrage opportunities."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import structlog

from engine.arb.routing.route_registry import ROUTES, ROUTES_BY_DIRECTION
from engine.types import TxResult
from engine.venues.base import DexExecutionVenue, is_dex_execution_venue, is_market_order_venue


logger = structlog.get_logger()


async def _resolve_recorded_sell(sell_venue: DexExecutionVenue, sell_tx_hash: str) -> TxResult:
    """Resolve a previously broadcast sell on-chain before recovery acts.

    Raises while no receipt exists: the tx may still land, so retrying or
    reversing now could execute the sell twice.
    """
    loop = asyncio.get_running_loop()
    sell_result = await loop.run_in_executor(
        None, sell_venue.check_transaction, sell_tx_hash, sell_venue.stable_address
    )
    if sell_result is None:
        raise ValueError(
            f"sell tx {sell_tx_hash} is broadcast but unconfirmed; recovering now could "
            f"execute the sell twice — wait for it to confirm or drop, then re-run /recover"
        )
    return sell_result


def _confirmed_sell_usd_out(
    sell_venue: DexExecutionVenue, sell_result: TxResult, sell_tx_hash: str
) -> Decimal:
    if sell_result.output_raw is None:
        raise ValueError(
            f"sell tx {sell_tx_hash} confirmed on-chain but its swap output "
            f"could not be parsed — record the outcome manually"
        )
    return Decimal(sell_result.output_raw) / Decimal(10 ** sell_venue.stable_decimals)


async def recover_dex_half_open(engine: Any, opp_id: str) -> dict[str, Any]:
    """Recover a half-open DEX-DEX arb."""
    if engine._arb_executing:
        raise ValueError("execution in progress")
    engine._arb_executing = True
    try:
        return await _recover_dex_half_open_inner(engine, opp_id)
    finally:
        engine._arb_executing = False


async def _recover_dex_half_open_inner(engine: Any, opp_id: str) -> dict[str, Any]:
    """Inner implementation of recover_dex_half_open, called with _arb_executing held."""
    arbitrage_store = engine.arbitrage_store
    opp = await arbitrage_store.get_dex_arbitrage_opportunity(opp_id)
    if opp is None:
        raise ValueError(f"Unknown DEX arbitrage opportunity: {opp_id}")
    if opp.status not in ("buy_filled", "half_open"):
        raise ValueError(f"Opportunity {opp_id} is not recoverable from status {opp.status}")

    direction = opp.direction
    route_def = ROUTES_BY_DIRECTION[direction]
    buy_venue_name = route_def.buy_leg.venue
    sell_venue_name = route_def.sell_leg.venue
    buy_venue = engine.venues[buy_venue_name]
    sell_venue = engine.venues[sell_venue_name]
    from engine.arb.execution.executor import _clean_revert

    loop = asyncio.get_running_loop()

    sell_cngn = opp.buy_amount_cngn
    if not sell_cngn:
        raise ValueError(
            f"Cannot recover {opp_id}: buy_amount_cngn not recorded "
            f"(old record — check buy tx {opp.buy_tx_hash} manually)"
        )

    # A recorded sell hash means a sell was already broadcast whose outcome was
    # unknown when the trade went half-open — resolve it on-chain before any
    # retry can sell the same inventory twice.
    if opp.sell_tx_hash and is_dex_execution_venue(sell_venue):
        sell_result = await _resolve_recorded_sell(sell_venue, opp.sell_tx_hash)
        if sell_result.status == "confirmed":
            usd_out = _confirmed_sell_usd_out(sell_venue, sell_result, opp.sell_tx_hash)
            cost_basis = opp.executed_size_usd if opp.executed_size_usd is not None else opp.optimal_size_usd
            actual_profit = usd_out - cost_basis
            reason = "Recovered: sell landed on-chain"
            await arbitrage_store.update_dex_arbitrage_execution_state(
                opp_id,
                status="completed",
                sell_tx_hash=opp.sell_tx_hash,
                reason=reason,
                actual_profit_usd=float(actual_profit),
            )
            engine.inventory.record_trade_complete(opp_id, cost_basis, actual_profit, Decimal("0"))
            await engine.history.record_executed_raw(
                opp_id=opp_id,
                pipeline="dex_dex",
                direction=opp.direction,
                buy_venue=buy_venue_name,
                sell_venue=sell_venue_name,
                optimal_size_usd=opp.optimal_size_usd,
                routed_size_usd=cost_basis,
                executed_size_usd=cost_basis,
                expected_profit_usd=opp.expected_profit_usd,
                net_spread_bps=opp.net_spread_bps,
                actual_profit_usd=actual_profit,
                reason=reason,
                buy_tx_hash=opp.buy_tx_hash,
                sell_tx_hash=opp.sell_tx_hash,
            )
            logger.info(
                "dex_dex_recovery_completed",
                opp_id=opp_id,
                method="sell_landed",
                sell_tx_hash=opp.sell_tx_hash,
                profit_usd=float(actual_profit),
            )
            return {
                "status": "completed",
                "method": "sell_landed",
                "opp_id": opp_id,
                "sell_tx_hash": opp.sell_tx_hash,
                "profit_usd": float(actual_profit),
            }
        # Reverted on-chain — the sell definitively failed; normal recovery below.

    can_retry_sell = False
    if is_dex_execution_venue(sell_venue):
        sell_amount_raw = int(sell_cngn * Decimal(10 ** sell_venue.cngn_decimals))
        sell_sim_err = await loop.run_in_executor(
            None, sell_venue.simulate_swap, sell_venue.cngn_address, sell_amount_raw, 0
        )
        can_retry_sell = sell_sim_err is None

    if can_retry_sell:
        logger.info(
            "dex_dex_recovery_retrying_sell",
            opp_id=opp_id,
            sell_venue=sell_venue_name,
            amount_cngn=float(sell_cngn),
        )
        sell_trade = await engine.executor.execute_dex_sell(sell_venue_name, sell_cngn, Decimal("0"), opp_id)
        if sell_trade and sell_trade.status != "failed":
            cost_basis = opp.executed_size_usd if opp.executed_size_usd is not None else opp.optimal_size_usd
            actual_profit = sell_trade.amount * (sell_trade.price or Decimal("0")) - cost_basis
            await arbitrage_store.update_dex_arbitrage_execution_state(
                opp_id,
                status="completed",
                sell_tx_hash=sell_trade.tx_hash,
                reason="Recovered: retried sell leg",
                actual_profit_usd=float(actual_profit),
            )
            engine.inventory.record_trade_complete(opp_id, cost_basis, actual_profit, Decimal("0"))
            await engine.history.record_executed_raw(
                opp_id=opp_id,
                pipeline="dex_dex",
                direction=opp.direction,
                buy_venue=buy_venue_name,
                sell_venue=sell_venue_name,
                optimal_size_usd=opp.optimal_size_usd,
                routed_size_usd=cost_basis,
                executed_size_usd=cost_basis,
                expected_profit_usd=opp.expected_profit_usd,
                net_spread_bps=opp.net_spread_bps,
                actual_profit_usd=actual_profit,
                reason="Recovered: retried sell leg",
                buy_tx_hash=opp.buy_tx_hash,
                sell_tx_hash=sell_trade.tx_hash,
            )
            logger.info(
                "dex_dex_recovery_completed",
                opp_id=opp_id,
                method="retry_sell",
                sell_tx_hash=sell_trade.tx_hash,
                profit_usd=float(actual_profit),
            )
            return {
                "status": "completed",
                "method": "retry_sell",
                "opp_id": opp_id,
                "sell_tx_hash": sell_trade.tx_hash,
                "profit_usd": float(actual_profit),
            }
        logger.warning(
            "dex_dex_recovery_sell_retry_failed",
            opp_id=opp_id,
            error=sell_trade.error if sell_trade else "unknown",
        )

    logger.warning(
        "dex_dex_recovery_reversing_buy",
        opp_id=opp_id,
        buy_venue=buy_venue_name,
        cngn_to_reverse=float(sell_cngn),
    )
    reverse_trade = await engine.executor.execute_dex_sell(buy_venue_name, sell_cngn, Decimal("0"), opp_id)

    if not reverse_trade or reverse_trade.status == "failed":
        err = _clean_revert((reverse_trade.error if reverse_trade else None) or "reverse sell failed")
        recovery_reason = f"RECOVERY_FAILED:{err}"
        cost_basis = opp.executed_size_usd if opp.executed_size_usd is not None else opp.optimal_size_usd
        await arbitrage_store.update_dex_arbitrage_execution_state(
            opp_id,
            status="half_open",
            reason=recovery_reason,
        )
        await engine.history.record_failed_raw(
            opp_id=opp_id,
            pipeline="dex_dex",
            direction=opp.direction,
            buy_venue=buy_venue_name,
            sell_venue=sell_venue_name,
            status="half_open",
            optimal_size_usd=opp.optimal_size_usd,
            routed_size_usd=cost_basis,
            executed_size_usd=cost_basis,
            expected_profit_usd=opp.expected_profit_usd,
            net_spread_bps=opp.net_spread_bps,
            reason=recovery_reason,
            buy_tx_hash=opp.buy_tx_hash,
            sell_tx_hash=opp.sell_tx_hash,
        )
        engine.inventory.trip_circuit_breaker(f"DEX-DEX recovery reversal failed: {opp_id}")
        engine.inventory.record_trade_failure(opp_id, f"RECOVERY_REVERSAL:{err}")
        raise ValueError(err)

    cost_basis = opp.executed_size_usd if opp.executed_size_usd is not None else opp.optimal_size_usd
    actual_loss = reverse_trade.amount * (reverse_trade.price or Decimal("0")) - cost_basis
    await arbitrage_store.update_dex_arbitrage_execution_state(
        opp_id,
        status="completed",
        sell_tx_hash=reverse_trade.tx_hash,
        reason="Recovered: reversed buy leg",
        actual_profit_usd=float(actual_loss),
    )
    engine.inventory.record_trade_complete(opp_id, cost_basis, actual_loss, Decimal("0"))
    await engine.history.record_executed_raw(
        opp_id=opp_id,
        pipeline="dex_dex",
        direction=opp.direction,
        buy_venue=buy_venue_name,
        sell_venue=sell_venue_name,
        optimal_size_usd=opp.optimal_size_usd,
        routed_size_usd=cost_basis,
        executed_size_usd=cost_basis,
        expected_profit_usd=opp.expected_profit_usd,
        net_spread_bps=opp.net_spread_bps,
        actual_profit_usd=actual_loss,
        reason="Recovered: reversed buy leg",
        buy_tx_hash=opp.buy_tx_hash,
        sell_tx_hash=reverse_trade.tx_hash,
    )
    logger.info(
        "dex_dex_recovery_completed",
        opp_id=opp_id,
        method="reverse_buy",
        sell_tx_hash=reverse_trade.tx_hash,
        profit_usd=float(actual_loss),
    )
    return {
        "status": "completed",
        "method": "reverse_buy",
        "opp_id": opp_id,
        "sell_tx_hash": reverse_trade.tx_hash,
        "profit_usd": float(actual_loss),
    }


async def recover_cex_half_open(engine: Any, opp_id: str) -> dict[str, Any]:
    """Recover a half-open CEX-DEX arb."""
    if engine._arb_executing:
        raise ValueError("execution in progress")
    engine._arb_executing = True
    try:
        arbitrage_store = engine.arbitrage_store
        opp = await arbitrage_store.get_arbitrage_opportunity(opp_id)
        if opp is None:
            raise ValueError(f"Unknown CEX-DEX arbitrage opportunity: {opp_id}")
        if opp.status != "half_open":
            raise ValueError(f"Opportunity {opp_id} is not recoverable from status {opp.status}")
        buy_venue_name = opp.buy_venue
        sell_venue_name = opp.sell_venue
        cex_route_def = next(
            (r for r in ROUTES if r.buy_leg.venue == buy_venue_name and r.sell_leg.venue == sell_venue_name),
            None,
        )
        if cex_route_def is None:
            raise ValueError(f"Unknown CEX route: {buy_venue_name} → {sell_venue_name}")
        cex_direction = cex_route_def.direction
        buy_is_cex = cex_route_def.buy_leg.leg_type == "api"
        buy_amount_cngn = opp.buy_amount_cngn
        if not buy_amount_cngn:
            raise ValueError(
                f"Cannot recover {opp_id}: buy_amount_cngn not recorded "
                f"(old record — check buy tx {opp.buy_tx_hash} manually)"
            )

        # Same double-execution guard as DEX-DEX: a recorded sell reference
        # (tx hash for DEX legs, venue trade id for API legs) must be resolved
        # before reversing the buy, or a late-landing sell plus the reversal
        # would leave the book net short cNGN.
        sell_is_cex = cex_route_def.sell_leg.leg_type == "api"
        if opp.sell_tx_hash and sell_is_cex:
            sell_venue = engine.venues[sell_venue_name]
            if not is_market_order_venue(sell_venue):
                raise TypeError(f"{sell_venue_name} is not a market-order venue")
            trade_result = await sell_venue.check_trade(opp.sell_tx_hash)
            if trade_result is None or trade_result.status == "pending":
                raise ValueError(
                    f"sell trade {opp.sell_tx_hash} is placed but unresolved; recovering now could "
                    f"execute the sell twice — wait for it to reach a terminal state, then re-run /recover"
                )
            if trade_result.status == "filled":
                usd_out = trade_result.executed_stable
                actual_profit = usd_out - opp.recommended_size_usd
                reason = "Recovered: sell trade filled"
                await arbitrage_store.update_arbitrage_opportunity(
                    opp_id,
                    status="completed",
                    actual_profit_usd=float(actual_profit),
                    reason=reason,
                )
                engine.inventory.record_trade_complete(opp_id, opp.recommended_size_usd, actual_profit, Decimal("0"))
                await engine.history.record_executed_raw(
                    opp_id=opp_id,
                    pipeline="cex_dex",
                    direction=cex_direction,
                    buy_venue=opp.buy_venue,
                    sell_venue=opp.sell_venue,
                    optimal_size_usd=opp.recommended_size_usd,
                    routed_size_usd=opp.recommended_size_usd,
                    executed_size_usd=opp.recommended_size_usd,
                    expected_profit_usd=opp.expected_profit_usd,
                    net_spread_bps=opp.net_spread_bps,
                    actual_profit_usd=actual_profit,
                    reason=reason,
                    buy_tx_hash=opp.buy_tx_hash,
                    sell_tx_hash=opp.sell_tx_hash,
                )
                logger.info("cex_dex_recovery_completed", opp_id=opp_id, method="sell_landed", profit_usd=float(actual_profit))
                return {"status": "completed", "method": "sell_landed", "opp_id": opp_id, "profit_usd": float(actual_profit)}
            # Terminal failure on the venue — the sell definitively failed; reverse the buy below.

        if opp.sell_tx_hash and not sell_is_cex:
            sell_venue = engine.venues[sell_venue_name]
            if not is_dex_execution_venue(sell_venue):
                raise TypeError(f"{sell_venue_name} is not a DEX execution venue")
            sell_result = await _resolve_recorded_sell(sell_venue, opp.sell_tx_hash)
            if sell_result.status == "confirmed":
                usd_out = _confirmed_sell_usd_out(sell_venue, sell_result, opp.sell_tx_hash)
                actual_profit = usd_out - opp.recommended_size_usd
                reason = "Recovered: sell landed on-chain"
                await arbitrage_store.update_arbitrage_opportunity(
                    opp_id,
                    status="completed",
                    actual_profit_usd=float(actual_profit),
                    reason=reason,
                )
                engine.inventory.record_trade_complete(opp_id, opp.recommended_size_usd, actual_profit, Decimal("0"))
                await engine.history.record_executed_raw(
                    opp_id=opp_id,
                    pipeline="cex_dex",
                    direction=cex_direction,
                    buy_venue=opp.buy_venue,
                    sell_venue=opp.sell_venue,
                    optimal_size_usd=opp.recommended_size_usd,
                    routed_size_usd=opp.recommended_size_usd,
                    executed_size_usd=opp.recommended_size_usd,
                    expected_profit_usd=opp.expected_profit_usd,
                    net_spread_bps=opp.net_spread_bps,
                    actual_profit_usd=actual_profit,
                    reason=reason,
                    buy_tx_hash=opp.buy_tx_hash,
                    sell_tx_hash=opp.sell_tx_hash,
                )
                logger.info(
                    "cex_dex_recovery_completed",
                    opp_id=opp_id,
                    method="sell_landed",
                    profit_usd=float(actual_profit),
                )
                return {
                    "status": "completed",
                    "method": "sell_landed",
                    "opp_id": opp_id,
                    "profit_usd": float(actual_profit),
                }
            # Reverted on-chain — the sell definitively failed; reverse the buy.

        return await _reverse_cex_recovery(engine, arbitrage_store, opp_id, opp, cex_direction, buy_is_cex, buy_amount_cngn)
    finally:
        engine._arb_executing = False


async def _reverse_cex_recovery(
    engine: Any,
    arbitrage_store: Any,
    opp_id: str,
    opp: Any,
    cex_direction: str,
    buy_is_cex: bool,
    buy_amount_cngn: Decimal,
) -> dict[str, Any]:
    """Execute a reversal trade to recover a half-open CEX arb and record the outcome."""
    buy_venue_name = opp.buy_venue
    if buy_is_cex:
        method = "reverse_cex_buy"
        logger.warning(
            "cex_dex_recovery_reversing_cex_buy",
            opp_id=opp_id,
            buy_venue=buy_venue_name,
            amount_cngn=float(buy_amount_cngn),
        )
        reverse_trade = await engine.executor.execute_cex_sell(
            buy_venue_name,
            buy_amount_cngn,
            opp.buy_price,
            opp_id,
        )
        fallback_price = opp.buy_price
    else:
        method = "reverse_dex_buy"
        logger.warning(
            "cex_dex_recovery_reversing_dex_buy",
            opp_id=opp_id,
            buy_venue=buy_venue_name,
            amount_cngn=float(buy_amount_cngn),
        )
        reverse_trade = await engine.executor.execute_dex_sell(
            buy_venue_name,
            buy_amount_cngn,
            Decimal("0"),
            opp_id,
        )
        fallback_price = Decimal("0")

    if not reverse_trade or reverse_trade.status == "failed":
        err = (reverse_trade.error if reverse_trade else None) or "reverse sell failed"
        recovery_reason = f"RECOVERY_FAILED:{err}"
        await arbitrage_store.update_arbitrage_opportunity(opp_id, status="half_open", reason=recovery_reason)
        await engine.history.record_failed_raw(
            opp_id=opp_id,
            pipeline="cex_dex",
            direction=cex_direction,
            buy_venue=opp.buy_venue,
            sell_venue=opp.sell_venue,
            status="half_open",
            optimal_size_usd=opp.recommended_size_usd,
            routed_size_usd=opp.recommended_size_usd,
            executed_size_usd=opp.recommended_size_usd,
            expected_profit_usd=opp.expected_profit_usd,
            net_spread_bps=opp.net_spread_bps,
            reason=recovery_reason,
            buy_tx_hash=opp.buy_tx_hash,
        )
        engine.broadcast({"type": "alert", "severity": "critical",
                          "message": (
                              f"CEX-DEX recovery reversal failed for {opp_id}: {err}. "
                              "Manual intervention required."
                          )})
        raise ValueError(err)

    reason = f"Recovered: reversed {'CEX' if buy_is_cex else 'DEX'} buy leg"
    actual_loss = reverse_trade.amount * (reverse_trade.price or fallback_price) - opp.recommended_size_usd
    await arbitrage_store.update_arbitrage_opportunity(
        opp_id,
        status="completed",
        actual_profit_usd=float(actual_loss),
        reason=reason,
    )
    engine.inventory.record_trade_complete(opp_id, opp.recommended_size_usd, actual_loss, Decimal("0"))
    await engine.history.record_executed_raw(
        opp_id=opp_id,
        pipeline="cex_dex",
        direction=cex_direction,
        buy_venue=opp.buy_venue,
        sell_venue=opp.sell_venue,
        optimal_size_usd=opp.recommended_size_usd,
        routed_size_usd=opp.recommended_size_usd,
        executed_size_usd=opp.recommended_size_usd,
        expected_profit_usd=opp.expected_profit_usd,
        net_spread_bps=opp.net_spread_bps,
        actual_profit_usd=actual_loss,
        reason=reason,
        buy_tx_hash=opp.buy_tx_hash,
        sell_tx_hash=reverse_trade.tx_hash if not buy_is_cex else None,
    )
    logger.info("cex_dex_recovery_completed", opp_id=opp_id, method=method, profit_usd=float(actual_loss))
    return {"status": "completed", "method": method, "opp_id": opp_id, "profit_usd": float(actual_loss)}
