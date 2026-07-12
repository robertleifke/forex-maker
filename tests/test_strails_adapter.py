"""StablesRail adapter invariants: price/depth mapping and the escrow trade lifecycle.

The non-obvious pins:
  1. Depth normalization — StablesRail denominates availableLiquidity in the
     asset the taker delivers (stable on buyOrders, cNGN on sellOrders); both
     sides must come out in stablecoin units per the Quidax depth convention.
  2. Lifecycle mapping — signing/settling are NOT failures; only
     completed/failed/expired are terminal, and a poll-budget exhaustion must
     surface the tradeId, never a silent zero-fill "failure".
"""

import pytest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import engine.venues.cex.strails as strails_module
from engine.venues.cex.strails import StrailsAdapter


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Routes GETs by path substring; records POSTs."""

    def __init__(self) -> None:
        self.get_routes: dict[str, dict | list] = {}
        self.post_payloads: list[dict] = []
        self.post_response: dict = {}

    async def get(self, url, params=None) -> _FakeResponse:
        for fragment, payload in self.get_routes.items():
            if fragment in str(url):
                if isinstance(payload, list):  # sequence of responses
                    return _FakeResponse(payload.pop(0) if len(payload) > 1 else payload[0])
                return _FakeResponse(payload)
        raise AssertionError(f"unrouted GET {url}")

    async def post(self, url, json=None) -> _FakeResponse:
        self.post_payloads.append(json)
        return _FakeResponse(self.post_response)


def _make_adapter(client: _FakeClient | None = None, alert_store=None) -> StrailsAdapter:
    adapter = object.__new__(StrailsAdapter)
    adapter.name = "strails"
    adapter.api_key = "test-key"
    adapter.pair = "CNGN-USDC"
    adapter.base_url = "https://beta.stablesrail.io/v1"
    adapter.alert_store = alert_store or SimpleNamespace(insert_alert=AsyncMock())
    adapter.stable_symbol = "usdc"
    adapter._token_amount_field = "usdcAmount"
    adapter.enabled = True
    adapter.paused = False
    adapter.cngn_decimals = 6
    adapter.stable_decimals = 6
    adapter._client = client or _FakeClient()
    return adapter


def _success(data: dict) -> dict:
    return {"status": "Success", "response_code": "00", "message": "ok", "data": data}


_LIVE_BOOK = _success({
    "pair": "CNGN-USDC",
    "buyOrders": [{
        "orderId": "order-b", "price": "1388.77", "availableLiquidity": "10018.595012",
        "minAmount": "500", "maxAmount": "15000000", "status": "active",
    }],
    "sellOrders": [{
        "orderId": "order-s", "price": "1389.33", "availableLiquidity": "14029879.907407",
        "minAmount": "500", "maxAmount": "15000000", "status": "active",
    }],
})


@pytest.fixture(autouse=True)
def instant_trade_polls(monkeypatch):
    monkeypatch.setattr(strails_module, "_TRADE_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(strails_module, "_TRADE_POLL_BUDGET_SECONDS", 0.5)


class TestMarketData:
    @pytest.mark.asyncio
    async def test_price_quote_in_cngn_per_stable(self):
        client = _FakeClient()
        client.get_routes["fx/orderbook/stats"] = _success({
            "stats": {"bestBidPrice": "1388.77", "bestAskPrice": "1389.33"},
        })
        quote = await _make_adapter(client).get_current_price()

        assert quote is not None
        assert quote.bid == Decimal("1388.77")
        assert quote.ask == Decimal("1389.33")
        assert quote.mid == (quote.bid + quote.ask) / 2

    @pytest.mark.asyncio
    async def test_empty_book_yields_no_price(self):
        """CNGN-USDT is empty today: stats omit best prices entirely."""
        client = _FakeClient()
        client.get_routes["fx/orderbook/stats"] = _success({
            "stats": {"totalBuyLiquidity": "0", "totalSellLiquidity": "0"},
        })
        assert await _make_adapter(client).get_current_price() is None

    @pytest.mark.asyncio
    async def test_depth_normalizes_both_sides_to_stable_units(self):
        """Buy-side liquidity is stable-denominated; sell-side is cNGN and must
        be converted through the order price (live book: both ≈ $10k)."""
        client = _FakeClient()
        client.get_routes["fx/orderbook?"] = _LIVE_BOOK
        client.get_routes["fx/orderbook&"] = _LIVE_BOOK
        client.get_routes["fx/orderbook"] = _LIVE_BOOK
        depth = await _make_adapter(client).get_order_book_depth()

        assert depth is not None
        assert depth.bids[0].price == Decimal("1388.77")
        assert depth.bids[0].amount == Decimal("10018.595012")
        assert depth.asks[0].price == Decimal("1389.33")
        # 14,029,879.907407 cNGN / 1389.33 ≈ 10,098 USDC
        assert depth.asks[0].amount == Decimal("14029879.907407") / Decimal("1389.33")

    @pytest.mark.asyncio
    async def test_inactive_orders_excluded_from_depth(self):
        book = _success({
            "buyOrders": [{"price": "1388.77", "availableLiquidity": "100", "status": "paused"}],
            "sellOrders": [],
        })
        client = _FakeClient()
        client.get_routes["fx/orderbook"] = book
        depth = await _make_adapter(client).get_order_book_depth()

        assert depth is not None
        assert depth.bids == [] and depth.asks == []


class TestTradeLifecycle:
    def _trading_client(self, status_sequence: list[dict]) -> _FakeClient:
        client = _FakeClient()
        client.post_response = _success({"tradeId": "trade-1", "status": "pending"})
        client.get_routes["fx/trade/status/trade-1"] = status_sequence
        client.get_routes["fx/orderbook/stats"] = _success({
            "stats": {"bestBidPrice": "1388.77", "bestAskPrice": "1389.33"},
        })
        return client

    @pytest.mark.asyncio
    async def test_settling_is_not_failure_and_completed_reports_fills(self):
        client = self._trading_client([
            _success({"status": "locked"}),
            _success({"status": "signing"}),
            _success({"status": "settling"}),
            _success({
                "status": "completed", "usdcAmount": "500.25", "price": "1389.33",
                "fintechNetAmount": "499.75",
            }),
        ])
        success, executed, price, error = await _make_adapter(client).market_sell_cngn(Decimal("695000"))

        assert success and error is None
        assert executed == Decimal("500.25")
        assert price == Decimal("1389.33")
        assert client.post_payloads[0]["side"] == "sell"
        assert client.post_payloads[0]["cngnAmount"] == "695000.000000"
        assert client.post_payloads[0]["idempotencyKey"].startswith("fxm-")

    @pytest.mark.asyncio
    async def test_buy_converts_stable_budget_through_ask(self):
        client = self._trading_client([
            _success({"status": "completed", "usdcAmount": "500", "price": "1389.33"}),
        ])
        success, _, _, _ = await _make_adapter(client).market_buy_cngn(Decimal("500"))

        assert success
        assert client.post_payloads[0]["side"] == "buy"
        # 500 USDC × 1389.33 ask, rounded down to 6 dp.
        assert client.post_payloads[0]["cngnAmount"] == "694665.000000"

    @pytest.mark.asyncio
    async def test_failed_trade_reports_error(self):
        client = self._trading_client([
            _success({"status": "failed", "errorMessage": "insufficient escrow balance"}),
        ])
        success, executed, price, error = await _make_adapter(client).market_sell_cngn(Decimal("695000"))

        assert not success
        assert (executed, price) == (Decimal("0"), Decimal("0"))
        assert error == "insufficient escrow balance"

    @pytest.mark.asyncio
    async def test_expired_lock_is_terminal_failure(self):
        client = self._trading_client([_success({"status": "expired", "errorMessage": None})])
        success, _, _, error = await _make_adapter(client).market_sell_cngn(Decimal("695000"))

        assert not success
        assert "expired" in error

    @pytest.mark.asyncio
    async def test_poll_budget_exhaustion_surfaces_trade_id_and_alerts(self):
        """A trade stuck in settling may still land — the tradeId must survive
        into the error and a critical alert, never a silent zero-fill."""
        alert_store = SimpleNamespace(insert_alert=AsyncMock())
        client = self._trading_client([_success({"status": "settling"})])
        adapter = _make_adapter(client, alert_store=alert_store)

        success, _, _, error = await adapter.market_sell_cngn(Decimal("695000"))

        assert not success
        assert "trade-1" in error and "may yet settle" in error
        alert_store.insert_alert.assert_awaited_once()
        assert alert_store.insert_alert.call_args.kwargs["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_rejected_order_returns_api_message(self):
        client = _FakeClient()
        client.post_response = {"status": "Failed", "response_code": "02", "message": "NO_LIQUIDITY"}
        success, _, _, error = await _make_adapter(client).market_sell_cngn(Decimal("695000"))

        assert not success
        assert error == "NO_LIQUIDITY"
