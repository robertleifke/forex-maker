"""Small helpers for normalizing Web3 hex/bytes boundary values."""

from __future__ import annotations

from string import hexdigits
from typing import Any, Iterator

from eth_typing import HexStr


def _looks_like_hex(value: str) -> bool:
    return bool(value) and all(char in hexdigits for char in value)


def coerce_hex_str(raw: Any) -> str:
    """Normalize bytes-like and Web3 hash values into a plain hex string."""
    value: str
    if isinstance(raw, str):
        value = raw
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        value = bytes(raw).hex()
    else:
        hex_method = getattr(raw, "hex", None)
        if callable(hex_method):
            value = str(hex_method())
        else:
            value = str(raw)

    if value.startswith(("0x", "0X")):
        return "0x" + value[2:].lower()
    if _looks_like_hex(value):
        return "0x" + value.lower()
    return value


def coerce_hex_bytes(raw: Any) -> bytes:
    """Normalize Web3 log/data payloads into raw bytes."""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)

    raw_str = coerce_hex_str(raw)
    hex_body = raw_str[2:] if raw_str.startswith("0x") else raw_str
    return bytes.fromhex(hex_body)


def as_hexstr(raw: str) -> HexStr:
    """Normalize a hex string for Web3 TypedDict request payloads."""
    return HexStr(coerce_hex_str(raw))


def log_scan_chunk_size(chain_id: int) -> int:
    """Per-chain block window for chunked eth_getLogs scans.

    BSC public RPCs cap getLogs ranges far more aggressively than Base; using a
    chain-aware window keeps each request both accepted and bounded in size.
    """
    return 5_000 if chain_id in (56, 97) else 50_000


def iter_block_chunks(
    from_block: int, to_block: int, chunk_size: int, *, descending: bool = False
) -> Iterator[tuple[int, int]]:
    """Yield inclusive (start, end) block ranges covering [from_block, to_block]
    in pieces of at most ``chunk_size`` blocks.

    Chunking bounds each eth_getLogs response so a full-history scan can never
    pull an unbounded blob into a single Python list. Ascending by default;
    ``descending`` yields the newest ranges first for early-terminating scans.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    if from_block > to_block:
        return
    if descending:
        end = to_block
        while end >= from_block:
            start = max(from_block, end - chunk_size + 1)
            yield (start, end)
            end = start - 1
    else:
        start = from_block
        while start <= to_block:
            end = min(to_block, start + chunk_size - 1)
            yield (start, end)
            start = end + 1
