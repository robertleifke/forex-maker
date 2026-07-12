"""StablesRail (Strails) adapter for the cNGN↔stablecoin FX orderbook.

StablesRail is a fintech-to-fintech P2P orderbook (pairs CNGN-USDC / CNGN-USDT)
with REST market orders and escrow-based settlement to an on-chain smart
wallet on Base. Prices are quoted in cNGN per stablecoin, matching the Quidax
PriceQuote convention. Note: https://beta.stablesrail.io/v1 is the *live
production* environment despite the name.

Execution prerequisites (not yet met as of 2026-07-12): the account's MPC
vault must be registered and auto-signing configured before /fx trades can
settle. Until a live canary trade verifies the market-order `side` semantics
and fee-net fill fields, this venue must not be added to the route registry.
"""

import asyncio
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional

import httpx
import structlog
from web3 import Web3

from engine.types import OrderBookDepth, OrderBookLevel, Position, PriceQuote
from engine.db.backend import AlertStoreProtocol
from engine.venues.base import VenueAdapter

logger = structlog.get_logger()

_TRADE_POLL_INTERVAL_SECONDS = 2.0
# Escrow price lock is 5 minutes; allow it to expire plus margin before giving up.
_TRADE_POLL_BUDGET_SECONDS = 330.0
_TERMINAL_TRADE_STATUSES = ("completed", "failed", "expired")

_ERC20_BALANCE_OF_ABI = [{
    "constant": True,
    "inputs": [{"name": "account", "type": "address"}],
    "name": "balanceOf",
    "outputs": [{"name": "", "type": "uint256"}],
    "type": "function",
}]


class StrailsAdapter(VenueAdapter):
    """Thin REST adapter for StablesRail FX orderbook trading.

    The venue's trading balance is its smart wallet on Base, so get_position
    reads ERC-20 balances on-chain — that wallet *is* the venue's native
    balance API (there is no REST balance endpoint for the fintech treasury).
    """

    def __init__(
        self,
        api_key: str,
        *,
        alert_store: AlertStoreProtocol,
        wallet_address: str,
        rpc_url: str,
        cngn_address: str,
        stable_address: str,
        pair: str = "CNGN-USDC",
        base_url: str = "https://beta.stablesrail.io/v1",
        name: str = "strails",
    ):
        self.name = name
        self.api_key = api_key
        self.pair = pair
        self.base_url = base_url.rstrip("/")
        self.alert_store = alert_store
        self.stable_symbol = pair.split("-")[1].lower()  # "usdc" / "usdt"
        # Dynamic token-amount field in trade responses, e.g. "usdcAmount".
        self._token_amount_field = f"{self.stable_symbol}Amount"
        self.enabled = True
        self.paused = False
        self._client: Optional[httpx.AsyncClient] = None

        self.wallet_address = Web3.to_checksum_address(wallet_address)
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._cngn_token = self.w3.eth.contract(
            address=Web3.to_checksum_address(cngn_address), abi=_ERC20_BALANCE_OF_ABI
        )
        self._stable_token = self.w3.eth.contract(
            address=Web3.to_checksum_address(stable_address), abi=_ERC20_BALANCE_OF_ABI
        )
        # cNGN and USDC/USDT are all 6 decimals on Base; StablesRail amounts
        # are also 6-dp strings.
        self.cngn_decimals = 6
        self.stable_decimals = 6

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                timeout=30,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = await self._get_client()
        response = await client.get(f"{self.base_url}/{path}", params=params)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if payload.get("status") != "Success":
            raise RuntimeError(f"Strails {path}: {payload.get('message', 'unknown error')}")
        data: dict[str, Any] = payload.get("data", {})
        return data

    # ------------------------------------------------------------------
    # Read-only market data
    # ------------------------------------------------------------------

    async def get_current_price(self) -> Optional[PriceQuote]:
        """Best *executable* prices in cNGN per stablecoin (Quidax convention).

        Derived from the orderbook, not /fx/orderbook/stats: the stats
        bestBid/bestAsk are LP reference prices, which fills never execute at
        (see get_order_book_depth).
        """
        depth = await self.get_order_book_depth()
        if depth is None or not depth.bids or not depth.asks:
            # Empty or one-sided book (CNGN-USDT is empty as of 2026-07).
            return None
        bid = depth.bids[0].price
        ask = depth.asks[0].price
        return PriceQuote(
            source=self.name,
            timestamp=int(time.time() * 1000),
            bid=bid,
            ask=ask,
            mid=(bid + ask) / 2,
        )

    async def get_order_book_depth(self, limit: int = 50) -> Optional[OrderBookDepth]:
        """Executable depth in the Quidax convention: bid = cNGN received per
        stablecoin sold, ask = cNGN paid per stablecoin bought, amounts in
        stablecoin units.

        Two StablesRail quirks are normalized here so downstream profit math
        needs no venue special-casing — both verified with pricing-only quotes
        against the live book on 2026-07-12 (13,890 cNGN: buy quoted
        10.047864 USDC at 1389.33 × 0.995; sell quoted 9.951896 USDC at
        1388.77 × 1.005):

        1. Orders are cNGN-sided and the LP `price` field is a reference, not
           executable. Takers acquiring cNGN cross *sellOrders* at
           price × (1 − spread%); takers disposing of cNGN cross *buyOrders*
           at price × (1 + spread%). Hence bids map from sellOrders and asks
           from buyOrders.
        2. `availableLiquidity` is denominated in the asset the LP delivers:
           cNGN on sellOrders (converted to stablecoin here), stablecoin on
           buyOrders. It is informational until match time per the API's note.
        """
        try:
            data = await self._get("fx/orderbook", {"pair": self.pair, "limit": min(limit, 100)})
            bids: list[OrderBookLevel] = []
            for order in data.get("sellOrders", []):
                level = self._executable_level(order, taker_receives_cngn=True)
                if level is not None:
                    bids.append(level)
            asks: list[OrderBookLevel] = []
            for order in data.get("buyOrders", []):
                level = self._executable_level(order, taker_receives_cngn=False)
                if level is not None:
                    asks.append(level)
            bids.sort(key=lambda level: level.price, reverse=True)
            asks.sort(key=lambda level: level.price)
            return OrderBookDepth(
                venue=self.name,
                pair=self.pair.replace("-", "/"),
                timestamp=int(time.time() * 1000),
                bids=bids,
                asks=asks,
            )
        except Exception as e:
            logger.error("strails_depth_fetch_failed", pair=self.pair, error=str(e))
            return None

    @staticmethod
    def _executable_level(order: dict[str, Any], *, taker_receives_cngn: bool) -> OrderBookLevel | None:
        if order.get("status") != "active":
            return None
        price = Decimal(str(order.get("price", "0")))
        liquidity = Decimal(str(order.get("availableLiquidity", "0")))
        spread_pct = Decimal(str(order.get("spread", "0")))
        if price <= 0 or liquidity <= 0:
            return None
        if taker_receives_cngn:
            executable = price * (1 - spread_pct / 100)
            return OrderBookLevel(price=executable, amount=liquidity / executable)
        executable = price * (1 + spread_pct / 100)
        return OrderBookLevel(price=executable, amount=liquidity)

    async def get_position(self) -> Position:
        """On-chain ERC-20 balances of the StablesRail smart wallet on Base."""
        loop = asyncio.get_running_loop()

        def _read_balances() -> dict[str, Decimal]:
            cngn_raw = self._cngn_token.functions.balanceOf(self.wallet_address).call()
            stable_raw = self._stable_token.functions.balanceOf(self.wallet_address).call()
            return {
                "cngn": Decimal(cngn_raw) / Decimal(10**self.cngn_decimals),
                self.stable_symbol: Decimal(stable_raw) / Decimal(10**self.stable_decimals),
            }

        balances = await loop.run_in_executor(None, _read_balances)
        return Position(
            venue=self.name,
            pair=self.pair.replace("-", "/"),
            timestamp=int(time.time() * 1000),
            balances=balances,
        )

    # ------------------------------------------------------------------
    # Market-order execution (MarketOrderVenue)
    #
    # BLOCKED until the account's MPC vault is registered, and gated on a
    # live canary trade verifying two documented-but-unproven assumptions:
    # side="buy" acquires cNGN (side refers to the base asset of CNGN-USDC),
    # and the dynamic token-amount field is the gross stablecoin leg.
    # ------------------------------------------------------------------

    async def market_buy_cngn(self, spend_stable: Decimal) -> tuple[bool, Decimal, Decimal, str | None]:
        """Acquire cNGN by spending `spend_stable` stablecoin.

        StablesRail denominates market orders in cNGN, so the stablecoin
        budget is converted through the best executable bid (the cNGN received
        per stablecoin when crossing the LP sell side) before submission.
        """
        quote = await self.get_current_price()
        if quote is None or quote.bid <= 0:
            return False, Decimal("0"), Decimal("0"), f"no {self.pair} bid price available"
        cngn_amount = (spend_stable * quote.bid).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        return await self._execute_market_order("buy", cngn_amount)

    async def market_sell_cngn(self, amount_cngn: Decimal) -> tuple[bool, Decimal, Decimal, str | None]:
        """Dispose of `amount_cngn` cNGN for stablecoin. Volume is cNGN natively."""
        return await self._execute_market_order(
            "sell", amount_cngn.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        )

    async def _execute_market_order(
        self, side: str, cngn_amount: Decimal
    ) -> tuple[bool, Decimal, Decimal, str | None]:
        client = await self._get_client()
        payload = {
            "pair": self.pair,
            "side": side,
            "cngnAmount": str(cngn_amount),
            "idempotencyKey": f"fxm-{uuid.uuid4()}",
        }
        try:
            response = await client.post(f"{self.base_url}/fx/market-order", json=payload)
            resp_json: dict[str, Any] = response.json()
        except Exception as e:
            return False, Decimal("0"), Decimal("0"), str(e)

        if resp_json.get("status") != "Success":
            return False, Decimal("0"), Decimal("0"), str(resp_json.get("message", "Unknown Strails error"))

        trade_id = str(resp_json.get("data", {}).get("tradeId", ""))
        if not trade_id:
            # Order accepted but no trade id — cannot track settlement; alert loudly.
            await self.alert_store.insert_alert(
                severity="critical",
                category="cex",
                message=f"Strails {self.name} market {side} accepted without a tradeId — reconcile manually",
            )
            return False, Decimal("0"), Decimal("0"), "market order accepted without tradeId"

        return await self._await_trade_terminal(trade_id, side)

    async def _await_trade_terminal(
        self, trade_id: str, side: str
    ) -> tuple[bool, Decimal, Decimal, str | None]:
        """Poll /fx/trade/status until the escrow lifecycle reaches a terminal state.

        Lifecycle: pending → locked → signing → settling → completed | failed |
        expired (5-minute price lock). A budget exhaustion is NOT a definitive
        failure — the trade may still settle — so it alerts critically and
        reports the tradeId for manual resolution. Automatic resolution of
        stuck trades (the CEX analog of the DEX pending-sell recovery gate)
        must exist before this venue joins the route registry.
        """
        deadline = time.monotonic() + _TRADE_POLL_BUDGET_SECONDS
        status = "unknown"
        while time.monotonic() < deadline:
            await asyncio.sleep(_TRADE_POLL_INTERVAL_SECONDS)
            try:
                data = await self._get(f"fx/trade/status/{trade_id}")
            except Exception as e:
                logger.warning("strails_trade_status_poll_failed", trade_id=trade_id, error=str(e))
                continue
            status = str(data.get("status", "unknown"))
            if status not in _TERMINAL_TRADE_STATUSES:
                continue
            if status == "completed":
                executed_stable = Decimal(str(data.get(self._token_amount_field, "0") or "0"))
                avg_price = Decimal(str(data.get("price", "0") or "0"))
                logger.info(
                    "strails_trade_completed",
                    trade_id=trade_id,
                    side=side,
                    executed_stable=float(executed_stable),
                    price=float(avg_price),
                    net_amount=data.get("fintechNetAmount"),
                )
                if executed_stable <= 0 or avg_price <= 0:
                    return False, Decimal("0"), Decimal("0"), (
                        f"trade {trade_id} completed but fill fields missing — reconcile manually"
                    )
                return True, executed_stable, avg_price, None
            error = str(data.get("errorMessage") or f"trade {status}")
            logger.error("strails_trade_failed", trade_id=trade_id, side=side, status=status, error=error)
            return False, Decimal("0"), Decimal("0"), error

        message = (
            f"Strails trade {trade_id} still '{status}' after {int(_TRADE_POLL_BUDGET_SECONDS)}s — "
            f"it may yet settle; resolve via /fx/trade/status/{trade_id} before any retry"
        )
        await self.alert_store.insert_alert(severity="critical", category="cex", message=message)
        return False, Decimal("0"), Decimal("0"), message
