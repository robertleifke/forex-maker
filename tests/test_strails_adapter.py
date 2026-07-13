"""StablesRail adapter invariants: executable-price mapping and the escrow trade lifecycle.

The non-obvious pins (all quote-verified against the live book 2026-07-12):
  1. Executable prices — LP `price` fields are references; takers acquiring
     cNGN cross *sellOrders* at price × (1 − spread%), takers disposing cNGN
     cross *buyOrders* at price × (1 + spread%). So bids map from sellOrders,
     asks from buyOrders, and depth/PriceQuote carry executable prices.
  2. Liquidity denomination — availableLiquidity is the asset the LP delivers
     (cNGN on sellOrders, stable on buyOrders); both sides normalize to
     stablecoin units per the Quidax depth convention.
  3. Lifecycle mapping — signing/settling are NOT failures; only
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
    adapter.destination_wallet = None
    adapter._client = client or _FakeClient()
    return adapter


def _success(data: dict) -> dict:
    return {"status": "Success", "response_code": "00", "message": "ok", "data": data}


_LIVE_BOOK = _success({
    "pair": "CNGN-USDC",
    "buyOrders": [{
        "orderId": "order-b", "price": "1388.77", "spread": 0.5,
        "availableLiquidity": "10018.595012",
        "minAmount": "500", "maxAmount": "15000000", "status": "active",
    }],
    "sellOrders": [{
        "orderId": "order-s", "price": "1389.33", "spread": 0.5,
        "availableLiquidity": "14029879.907407",
        "minAmount": "500", "maxAmount": "15000000", "status": "active",
    }],
})

# Executable prices verified via pricing-only quotes 2026-07-12:
# buy 13890 cNGN → 10.047864 USDC (1389.33 × 0.995); sell → 9.951896 USDC (1388.77 × 1.005).
_EXEC_BID = Decimal("1389.33") * Decimal("0.995")   # 1382.383350
_EXEC_ASK = Decimal("1388.77") * Decimal("1.005")   # 1395.713850


@pytest.fixture(autouse=True)
def instant_trade_polls(monkeypatch):
    monkeypatch.setattr(strails_module, "_TRADE_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(strails_module, "_TRADE_POLL_BUDGET_SECONDS", 0.5)


class TestProxyPlumbing:
    @pytest.mark.asyncio
    async def test_socks_proxy_builds_client(self):
        """Dev machines tunnel through the allowlisted VPS via ssh -D (SOCKS5).
        Pins the httpx `proxy=` kwarg and the httpx[socks] extra — either
        drifting would break every dev StablesRail call at client creation."""
        adapter = StrailsAdapter(
            api_key="test-key",
            alert_store=SimpleNamespace(insert_alert=AsyncMock()),
            wallet_address="0x7DB25C4Bd88Fd07aDf0585348c97f0C1BA7dC6a9",
            rpc_url="http://localhost:9",  # never contacted in this test
            cngn_address="0x46C85152bFe9f96829aA94755D9f915F9B10EF5F",
            stable_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            proxy="socks5://localhost:1080",
        )
        client = await adapter._get_client()
        assert client is not None
        await adapter.close()


class TestMarketData:
    @pytest.mark.asyncio
    async def test_price_quote_is_executable_not_reference(self):
        """PriceQuote carries executable prices: bid from sellOrders × (1−s),
        ask from buyOrders × (1+s) — never the LP reference prices."""
        client = _FakeClient()
        client.get_routes["fx/orderbook"] = _LIVE_BOOK
        quote = await _make_adapter(client).get_current_price()

        assert quote is not None
        assert quote.bid == _EXEC_BID
        assert quote.ask == _EXEC_ASK
        assert quote.bid < quote.ask
        assert quote.mid == (quote.bid + quote.ask) / 2

    @pytest.mark.asyncio
    async def test_empty_book_yields_no_price(self):
        """CNGN-USDT is empty today: no orders on either side."""
        client = _FakeClient()
        client.get_routes["fx/orderbook"] = _success({"buyOrders": [], "sellOrders": []})
        assert await _make_adapter(client).get_current_price() is None

    @pytest.mark.asyncio
    async def test_depth_swaps_sides_and_normalizes_to_stable_units(self):
        """Bids come from sellOrders (cNGN liquidity ÷ executable price);
        asks from buyOrders (stable liquidity as-is). Live book: both ≈ $10k."""
        client = _FakeClient()
        client.get_routes["fx/orderbook"] = _LIVE_BOOK
        depth = await _make_adapter(client).get_order_book_depth()

        assert depth is not None
        assert depth.bids[0].price == _EXEC_BID
        assert depth.bids[0].amount == Decimal("14029879.907407") / _EXEC_BID
        assert depth.asks[0].price == _EXEC_ASK
        assert depth.asks[0].amount == Decimal("10018.595012")

    @pytest.mark.asyncio
    async def test_inactive_orders_excluded_from_depth(self):
        book = _success({
            "buyOrders": [{"price": "1388.77", "spread": 0.5, "availableLiquidity": "100", "status": "paused"}],
            "sellOrders": [],
        })
        client = _FakeClient()
        client.get_routes["fx/orderbook"] = book
        depth = await _make_adapter(client).get_order_book_depth()

        assert depth is not None
        assert depth.bids == [] and depth.asks == []


class TestTradeLifecycle:
    def _trading_client(self, status_sequence: list[dict]) -> _FakeClient:
        """Trade status is polled via the trades LIST — the documented
        /fx/trade/status/:id endpoint 404s on the live API (2026-07-13)."""
        client = _FakeClient()
        client.post_response = _success({"tradeId": "trade-1", "status": "pending"})
        client.get_routes["fx/trades"] = [
            _success({"trades": [dict(payload["data"], tradeId="trade-1")], "count": 1})
            for payload in status_sequence
        ]
        client.get_routes["fx/orderbook"] = _LIVE_BOOK
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
    async def test_buy_converts_stable_budget_through_executable_bid(self):
        client = self._trading_client([
            _success({"status": "completed", "usdcAmount": "500", "price": "1382.38"}),
        ])
        success, _, _, _ = await _make_adapter(client).market_buy_cngn(Decimal("500"))

        assert success
        assert client.post_payloads[0]["side"] == "buy"
        # 500 USDC × executable bid (1389.33 × 0.995), rounded down to 6 dp —
        # sizing through the reference ask would overshoot the budget by the spread.
        assert client.post_payloads[0]["cngnAmount"] == "691191.675000"

    @pytest.mark.asyncio
    async def test_destination_wallet_included_when_configured(self):
        client = self._trading_client([
            _success({"status": "completed", "usdcAmount": "500", "price": "1382.38"}),
        ])
        adapter = _make_adapter(client)
        adapter.destination_wallet = "0x2D724867d3AeD4A9F09c096B87F939285DD3AE2D"
        await adapter.market_sell_cngn(Decimal("695000"))

        assert client.post_payloads[0]["destinationWalletAddress"] == adapter.destination_wallet

    @pytest.mark.asyncio
    async def test_destination_wallet_omitted_by_default(self):
        client = self._trading_client([
            _success({"status": "completed", "usdcAmount": "500", "price": "1382.38"}),
        ])
        adapter = _make_adapter(client)
        adapter.destination_wallet = None
        await adapter.market_sell_cngn(Decimal("695000"))

        assert "destinationWalletAddress" not in client.post_payloads[0]

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
    async def test_missing_trade_id_reconciles_via_trades_list(self):
        """Live API quirk (2026-07-13 canary): market-order Success responses can
        omit the tradeId — but the trade exists. The adapter must recover it from
        the trades list rather than misreport a live trade as a no-trade."""
        client = _FakeClient()
        client.post_response = _success({})  # accepted, no tradeId anywhere
        client.get_routes["fx/trades"] = _success({"trades": [{
            "tradeId": "trade-9", "side": "sell", "cngnAmount": "695000.000000",
            "status": "completed", "usdcAmount": "500.25", "price": "1389.33",
        }], "count": 1})
        client.get_routes["fx/orderbook"] = _LIVE_BOOK

        success, executed, price, error = await _make_adapter(client).market_sell_cngn(Decimal("695000"))

        assert success and error is None
        assert (executed, price) == (Decimal("500.25"), Decimal("1389.33"))

    @pytest.mark.asyncio
    async def test_trade_id_from_nested_trade_object(self):
        client = self._trading_client([
            _success({"status": "completed", "usdcAmount": "500", "price": "1382.38"}),
        ])
        client.post_response = _success({"trade": {"tradeId": "trade-1", "status": "pending"}})

        success, _, _, error = await _make_adapter(client).market_sell_cngn(Decimal("695000"))

        assert success and error is None

    @pytest.mark.asyncio
    async def test_rejected_order_returns_api_message(self):
        client = _FakeClient()
        client.post_response = {"status": "Failed", "response_code": "02", "message": "NO_LIQUIDITY"}
        success, _, _, error = await _make_adapter(client).market_sell_cngn(Decimal("695000"))

        assert not success
        assert error == "NO_LIQUIDITY"
