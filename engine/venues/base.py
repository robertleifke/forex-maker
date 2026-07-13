"""Base classes and capability protocols for venue adapters."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Optional, Protocol, TypeGuard

from engine.types import MarketOrderResult, Position, PriceQuote, TxResult


class VenueAdapter(ABC):
    """Abstract base class for all venue adapters."""

    name: str
    enabled: bool = True
    paused: bool = False

    @abstractmethod
    async def get_position(self) -> Position: ...

    @abstractmethod
    async def get_current_price(self) -> Optional[PriceQuote]: ...

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


class DexExecutionVenue(Protocol):
    """DEX-only execution surface shared across arb modules."""

    stable_address: str
    cngn_address: str
    stable_decimals: int
    cngn_decimals: int
    stable_token: Any
    cngn_token: Any
    trade_account: Any

    async def get_current_price(self) -> Optional[PriceQuote]: ...
    async def swap(self, token_in: str, amount_in: int, min_amount_out: int) -> TxResult: ...
    def simulate_swap(self, token_in: str, amount_in: int, min_amount_out: int) -> str | None: ...
    def check_transaction(self, tx_hash: str, output_token: str | None = None) -> TxResult | None: ...
    async def ensure_trade_approvals(self) -> None: ...


class MarketOrderVenue(Protocol):
    """REST venue that fills cNGN market orders — the execution surface of `api` arb legs.

    Executed volume is always the stablecoin amount and price always cNGN per
    stablecoin, regardless of how the exchange denominates the underlying
    order — exchange-specific mapping lives in the adapter. check_trade
    resolves a previously placed trade by its trade_ref: a terminal
    MarketOrderResult, or None while the outcome is still unobservable (the
    recovery gate for pending results — never retry or reverse past a None).
    """

    name: str

    async def market_buy_cngn(self, spend_stable: Decimal) -> MarketOrderResult: ...
    async def market_sell_cngn(self, amount_cngn: Decimal) -> MarketOrderResult: ...
    async def check_trade(self, trade_ref: str) -> MarketOrderResult | None: ...


def is_market_order_venue(venue: VenueAdapter) -> TypeGuard[MarketOrderVenue]:
    """Return True when a venue exposes the cNGN market-order execution surface."""
    return all(hasattr(venue, attr) for attr in ("market_buy_cngn", "market_sell_cngn", "check_trade"))


class SyncOrderLadderVenue(Protocol):
    async def sync_order_ladder(self, reference_price_ngn: Decimal) -> None: ...


class DepthVenue(Protocol):
    async def get_order_book_depth(self, limit: int = 50) -> Any: ...
    async def get_position(self) -> Any: ...


class WebhookVenue(Protocol):
    async def handle_webhook(self, event: dict[str, Any]) -> None: ...


def is_dex_execution_venue(venue: VenueAdapter) -> TypeGuard[DexExecutionVenue]:
    """Return True when a venue exposes the on-chain execution surface."""
    return all(
        hasattr(venue, attr)
        for attr in (
            "swap",
            "simulate_swap",
            "check_transaction",
            "stable_address",
            "cngn_address",
            "stable_decimals",
            "cngn_decimals",
            "stable_token",
            "cngn_token",
            "trade_account",
            "ensure_trade_approvals",
        )
    )
