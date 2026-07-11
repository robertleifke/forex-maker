"""Tests for BaseV4DexAdapter._send_transaction outcome classification.

A transaction that was broadcast must never be reported as hash="" /
status="failed": that erases a live on-chain trade and every downstream
consumer (half-open persistence, recovery sizing, /recover) then works
from a false "never happened" premise. Covers:
  1. Receipt wait timeout (TimeExhausted) → status "pending" with the real hash
  2. Timeout but the tx landed → confirmed via the direct receipt check
  3. Pre-broadcast failure → the only case where hash="" / "failed" is correct
  4. check_transaction receipt lookup for recovery
"""

import pytest
from types import SimpleNamespace

from web3.exceptions import TimeExhausted, TransactionNotFound

from engine.venues.dex.v4 import BaseV4DexAdapter

TX_HASH = "0x" + "ab" * 32


class _FakeEth:
    def __init__(self, wait_error=None, receipt=None, direct_receipt=None, send_error=None):
        self._wait_error = wait_error
        self._receipt = receipt
        self._direct_receipt = direct_receipt  # from get_transaction_receipt; exception to raise
        self._send_error = send_error
        self.sent = []

    def get_transaction_count(self, address, block_identifier):
        return 7

    def send_raw_transaction(self, raw):
        if self._send_error is not None:
            raise self._send_error
        self.sent.append(raw)
        return TX_HASH

    def wait_for_transaction_receipt(self, tx_hash, timeout=None):
        if self._wait_error is not None:
            raise self._wait_error
        return self._receipt

    def get_transaction_receipt(self, tx_hash):
        if isinstance(self._direct_receipt, Exception):
            raise self._direct_receipt
        return self._direct_receipt


def _adapter(eth):
    adapter = object.__new__(BaseV4DexAdapter)
    adapter.name = "uni-test"
    adapter.w3 = SimpleNamespace(eth=eth)
    adapter._nonce_locks = {}
    return adapter


def _account():
    return SimpleNamespace(
        address="0xACC0000000000000000000000000000000000001",
        sign_transaction=lambda tx: SimpleNamespace(rawTransaction=b"raw"),
    )


@pytest.mark.asyncio
async def test_receipt_timeout_returns_pending_with_hash():
    """TimeExhausted after broadcast must surface the real hash, not hash=''/failed."""
    eth = _FakeEth(
        wait_error=TimeExhausted("timed out"),
        direct_receipt=TransactionNotFound("not found"),
    )
    result = await _adapter(eth)._send_transaction({}, _account())

    assert result.hash == TX_HASH
    assert result.status == "pending"
    assert "unconfirmed" in (result.error or "")


@pytest.mark.asyncio
async def test_receipt_timeout_but_tx_landed_returns_confirmed():
    """If the tx confirmed right at the timeout edge, the direct receipt check finds it."""
    eth = _FakeEth(
        wait_error=TimeExhausted("timed out"),
        direct_receipt={"status": 1, "gasUsed": 90000, "logs": []},
    )
    result = await _adapter(eth)._send_transaction({}, _account())

    assert result.hash == TX_HASH
    assert result.status == "confirmed"
    assert result.gas_used == 90000


@pytest.mark.asyncio
async def test_broadcast_failure_is_the_only_hashless_failure():
    eth = _FakeEth(send_error=ValueError("nonce too low"))
    result = await _adapter(eth)._send_transaction({}, _account())

    assert result.hash == ""
    assert result.status == "failed"
    assert "nonce too low" in (result.error or "")


@pytest.mark.asyncio
async def test_reverted_receipt_keeps_hash():
    eth = _FakeEth(receipt={"status": 0, "gasUsed": 50000, "logs": []})
    result = await _adapter(eth)._send_transaction({}, _account())

    assert result.hash == TX_HASH
    assert result.status == "failed"


def test_check_transaction_returns_none_while_unconfirmed():
    eth = _FakeEth(direct_receipt=TransactionNotFound("not found"))
    assert _adapter(eth).check_transaction(TX_HASH) is None


def test_check_transaction_resolves_confirmed_receipt():
    eth = _FakeEth(direct_receipt={"status": 1, "gasUsed": 80000, "logs": []})
    result = _adapter(eth).check_transaction(TX_HASH)

    assert result is not None
    assert result.status == "confirmed"
    assert result.hash == TX_HASH


def test_check_transaction_resolves_reverted_receipt():
    eth = _FakeEth(direct_receipt={"status": 0, "gasUsed": 80000, "logs": []})
    result = _adapter(eth).check_transaction(TX_HASH)

    assert result is not None
    assert result.status == "failed"
