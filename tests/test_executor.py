"""Tests for ArbitrageExecutor leg methods (execute_dex_buy/sell, execute_cex_buy/sell)."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from engine.types import TxResult, PriceQuote
from engine.arb.execution.executor import ArbitrageExecutor
from tests.fakes import FakeCexAdapter, FakeDexAdapter


def _make_dex_venue(price=Decimal("0.000610"), swap_ok=True):
    venue = FakeDexAdapter()
    venue.stable_address = "0xusdc"
    venue.cngn_address = "0xcngn"
    venue.stable_decimals = 6
    venue.cngn_decimals = 6

    async def _get_price():
        return PriceQuote(source="test", timestamp=0, bid=price, ask=price, mid=price)

    async def _swap(token_in, amount_in, min_out):
        if swap_ok:
            return TxResult(hash="0xabc", status="confirmed", output_raw=amount_in)
        return TxResult(hash="", status="failed", error="swap failed")

    venue.get_current_price = _get_price
    venue.swap = _swap
    return venue


class TestExecuteDexBuy:
    @pytest.mark.asyncio
    async def test_success(self):
        venue = _make_dex_venue()
        executor = ArbitrageExecutor(venues={"uni-base": venue})
        trade = await executor.execute_dex_buy("uni-base", Decimal("500"), "opp-1")
        assert trade is not None
        assert trade.status == "confirmed"
        assert trade.side == "buy"

    @pytest.mark.asyncio
    async def test_swap_failure(self):
        venue = _make_dex_venue(swap_ok=False)
        executor = ArbitrageExecutor(venues={"uni-base": venue})
        trade = await executor.execute_dex_buy("uni-base", Decimal("500"), "opp-1")
        assert trade.status == "failed"

    @pytest.mark.asyncio
    async def test_no_price_quote(self):
        venue = _make_dex_venue()
        venue.get_current_price = AsyncMock(return_value=None)
        executor = ArbitrageExecutor(venues={"uni-base": venue})
        trade = await executor.execute_dex_buy("uni-base", Decimal("500"), "opp-1")
        assert trade.status == "failed"
        assert "price" in (trade.error or "").lower()


class TestExecuteDexSell:
    @pytest.mark.asyncio
    async def test_success(self):
        venue = _make_dex_venue()
        executor = ArbitrageExecutor(venues={"uni-base": venue})
        trade = await executor.execute_dex_sell("uni-base", Decimal("800000"), Decimal("490"), "opp-1")
        assert trade.status == "confirmed"
        assert trade.side == "sell"

    @pytest.mark.asyncio
    async def test_swap_failure(self):
        venue = _make_dex_venue(swap_ok=False)
        executor = ArbitrageExecutor(venues={"uni-base": venue})
        trade = await executor.execute_dex_sell("uni-base", Decimal("800000"), Decimal("490"), "opp-1")
        assert trade.status == "failed"


class TestExecuteCexBuy:
    @pytest.mark.asyncio
    async def test_success(self):
        cex = FakeCexAdapter(buy_success=True)
        executor = ArbitrageExecutor(venues={"quidax": cex})
        trade = await executor.execute_cex_buy("quidax", Decimal("500"), Decimal("0.000606"), "opp-1")
        assert trade.status == "submitted"
        assert trade.side == "buy"

    @pytest.mark.asyncio
    async def test_failure(self):
        cex = FakeCexAdapter(buy_success=False)
        executor = ArbitrageExecutor(venues={"quidax": cex})
        trade = await executor.execute_cex_buy("quidax", Decimal("500"), Decimal("0.000606"), "opp-1")
        assert trade.status == "failed"

    @pytest.mark.asyncio
    async def test_acquiring_cngn_sells_usdt_volume_in_usdt(self):
        """Buying cNGN on the usdtcngn market is a USDT *sell*; volume is the USDT spent."""
        cex = FakeCexAdapter(buy_success=True)
        executor = ArbitrageExecutor(venues={"quidax": cex})
        trade = await executor.execute_cex_buy("quidax", Decimal("500"), Decimal("0.000606"), "opp-1")
        assert cex.market_order_calls == [("sell", Decimal("500"))]
        # amount is denominated in cNGN, price in USD per cNGN.
        assert trade.amount == Decimal("500") * Decimal("1639.34")
        assert trade.price == Decimal(1) / Decimal("1639.34")


class TestExecuteCexSell:
    @pytest.mark.asyncio
    async def test_success(self):
        cex = FakeCexAdapter(sell_success=True)
        executor = ArbitrageExecutor(venues={"quidax": cex})
        trade = await executor.execute_cex_sell("quidax", Decimal("800000"), Decimal("0.000610"), "opp-1")
        assert trade.status == "submitted"
        assert trade.side == "sell"

    @pytest.mark.asyncio
    async def test_failure(self):
        cex = FakeCexAdapter(sell_success=False)
        executor = ArbitrageExecutor(venues={"quidax": cex})
        trade = await executor.execute_cex_sell("quidax", Decimal("800000"), Decimal("0.000610"), "opp-1")
        assert trade.status == "failed"

    @pytest.mark.asyncio
    async def test_disposing_cngn_buys_usdt_with_volume_in_cngn(self):
        """Selling cNGN is a USDT *buy* whose volume is the cNGN to spend.

        Quidax denominates market-buy volume in the quote asset (cNGN), unlike
        market sells (base USDT) — verified against live fills 2026-07-09.
        Regression for the July 2026 half-open failures: sizing the volume in
        USDT (~$200) made Quidax read it as ~200 cNGN, under the market's
        minimum order value, rejecting every sell leg with 110112
        "Price is below allowed minimum".
        """
        cex = FakeCexAdapter(sell_success=True)
        executor = ArbitrageExecutor(venues={"quidax": cex})
        amount_cngn = Decimal("800000")
        await executor.execute_cex_sell("quidax", amount_cngn, Decimal("0.000610"), "opp-1")

        assert cex.market_order_calls == [("buy", amount_cngn)]
