import asyncio
import time
import json
from decimal import Decimal
from pathlib import Path

import pytest

import engine.market.dex_volume as dex_volume
from engine.market.dex_volume import (
    RollingDexVolumeStore,
    _scan_pool_window_from_rpc,
    _refresh_pool,
    stable_volume_usd_from_v4_swap,
)
from engine.venues.dex.uniswap_base import UNISWAP_BASE_POOL_READ_CONFIG
from engine.venues.dex.uniswap_bsc import UNISWAP_BSC_POOL_READ_CONFIG


def _word(value: int) -> bytes:
    return int(value).to_bytes(32, "big", signed=True)


def test_stable_volume_from_base_swap_uses_amount1():
    amount0 = -103_427_340_000  # cNGN raw (6 decimals)
    amount1 = 73_649_100        # USDC raw (6 decimals)
    payload = b"".join([
        _word(amount0),
        _word(amount1),
        bytes(32),
        bytes(32),
        bytes(32),
        bytes(32),
    ])

    volume = stable_volume_usd_from_v4_swap(payload, UNISWAP_BASE_POOL_READ_CONFIG)
    assert volume == Decimal("73.6491")


def test_stable_volume_from_bsc_swap_uses_amount0():
    amount0 = 73_577_300_000_000_000_000  # USDT raw (18 decimals)
    amount1 = -103_427_340_000            # cNGN raw (6 decimals)
    payload = b"".join([
        _word(amount0),
        _word(amount1),
        bytes(32),
        bytes(32),
        bytes(32),
        bytes(32),
    ])

    volume = stable_volume_usd_from_v4_swap(payload, UNISWAP_BSC_POOL_READ_CONFIG)
    assert volume == Decimal("73.5773")


def test_rolling_store_evicts_old_swaps_and_persists_seeded_state(tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)
    too_old = now_ms - (24 * 60 * 60 * 1000) - 1

    store.record("pool", Decimal("10"), timestamp_ms=too_old, event_id="tx1:0", allow_unseeded=True)
    store.record("pool", Decimal("25"), timestamp_ms=now_ms, event_id="tx2:0", allow_unseeded=True)
    store.mark_seeded("pool")

    store.maybe_save(force=True)
    reloaded = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    reloaded.load()

    assert reloaded.get_24h_volume_usd("pool") == Decimal("25")
    assert reloaded.is_seeded("pool") is True


def test_unseeded_pool_volume_stays_hidden_and_live_updates_are_ignored(tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)

    store.record("pool", Decimal("99.99"), timestamp_ms=now_ms)
    assert store.get_24h_volume_usd("pool") == Decimal("0")
    assert store.is_seeded("pool") is False

    store.record("pool", Decimal("99.99"), timestamp_ms=now_ms, event_id="tx1:0", allow_unseeded=True)
    assert store.get_24h_volume_usd("pool") == Decimal("0")
    store.mark_seeded("pool")
    assert store.get_24h_volume_usd("pool") == Decimal("99.99")


def test_reset_clears_partial_unseeded_state(tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)

    store.record("pool", Decimal("25"), timestamp_ms=now_ms, event_id="tx1:0", allow_unseeded=True)
    assert store.is_seeded("pool") is False

    store.reset("pool")
    assert store.is_seeded("pool") is False
    assert store.get_24h_volume_usd("pool") == Decimal("0")


def test_duplicate_event_id_is_not_double_counted(tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)

    store.record("pool", Decimal("50"), timestamp_ms=now_ms, event_id="tx1:0", allow_unseeded=True)
    store.mark_seeded("pool")
    store.record("pool", Decimal("50"), timestamp_ms=now_ms, event_id="tx1:0")

    assert store.get_24h_volume_usd("pool") == Decimal("50")


def test_legacy_store_forces_reseed_before_exposing_volume(tmp_path):
    path = Path(tmp_path) / "dex_volume.json"
    path.write_text(json.dumps({
        "pool": {
            "seeded": True,
            "swaps": [[int(time.time() * 1000), "25"]],
        }
    }))

    store = RollingDexVolumeStore(path)
    assert store.is_seeded("pool") is False
    assert store.get_24h_volume_usd("pool") == Decimal("0")


def test_refresh_failure_hides_seeded_volume(monkeypatch, tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)
    store.record("pool", Decimal("25"), timestamp_ms=now_ms, event_id="tx1:0", allow_unseeded=True)
    store.mark_seeded("pool")

    monkeypatch.setattr(dex_volume, "_STORE", store)

    async def _boom(config, rpc_url: str, from_ts: float | None = None):
        raise RuntimeError("fail")

    monkeypatch.setattr(dex_volume, "_scan_pool_window_from_rpc", _boom)
    monkeypatch.setattr(dex_volume, "_rpc_candidates", lambda config: ["rpc1"])

    class _Config:
        pool_address = "pool"
        rpc_url = "rpc1"
        chain_id_str = "base"

    with pytest.raises(RuntimeError, match="fail"):
        asyncio.run(_refresh_pool(_Config()))

    assert store.is_seeded("pool") is False
    assert store.get_24h_volume_usd("pool") == Decimal("0")


def test_refresh_failure_does_not_leave_partial_volume_visible(monkeypatch, tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)
    store.record("pool", Decimal("25"), timestamp_ms=now_ms, event_id="tx1:0", allow_unseeded=True)
    store.mark_seeded("pool")

    monkeypatch.setattr(dex_volume, "_STORE", store)

    async def _partial(config, rpc_url: str, from_ts: float | None = None):
        staged = dex_volume._PoolVolumeState()
        staged.swaps.append((now_ms, Decimal("10"), "tx2:0"))
        staged.total_usd = Decimal("10")
        staged.event_ids.add("tx2:0")
        raise RuntimeError("midway fail")

    monkeypatch.setattr(dex_volume, "_scan_pool_window_from_rpc", _partial)
    monkeypatch.setattr(dex_volume, "_rpc_candidates", lambda config: ["rpc1"])

    class _Config:
        pool_address = "pool"
        rpc_url = "rpc1"
        chain_id_str = "base"

    with pytest.raises(RuntimeError, match="midway fail"):
        asyncio.run(_refresh_pool(_Config()))

    assert store.is_seeded("pool") is False
    assert store.get_24h_volume_usd("pool") == Decimal("0")


def test_refresh_keeps_last_good_volume_visible_until_new_window_is_ready(monkeypatch, tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)
    store.record("pool", Decimal("25"), timestamp_ms=now_ms, event_id="tx1:0", allow_unseeded=True)
    store.mark_seeded("pool")

    monkeypatch.setattr(dex_volume, "_STORE", store)
    observed: dict[str, Decimal | bool] = {}

    async def _scan(config, rpc_url: str, from_ts: float | None = None):
        observed["seeded_during_scan"] = store.is_seeded("pool")
        observed["volume_during_scan"] = store.get_24h_volume_usd("pool")
        staged = dex_volume._PoolVolumeState()
        staged.swaps.append((now_ms, Decimal("40"), "tx2:0"))
        staged.total_usd = Decimal("40")
        staged.event_ids.add("tx2:0")
        return staged

    monkeypatch.setattr(dex_volume, "_scan_pool_window_from_rpc", _scan)
    monkeypatch.setattr(dex_volume, "_rpc_candidates", lambda config: ["rpc1"])

    class _Config:
        pool_address = "pool"
        rpc_url = "rpc1"
        chain_id_str = "base"

    asyncio.run(_refresh_pool(_Config()))

    assert observed["seeded_during_scan"] is True
    assert observed["volume_during_scan"] == Decimal("25")
    assert store.is_seeded("pool") is True
    assert store.get_24h_volume_usd("pool") == Decimal("40")


class _SyncConfig:
    pool_address = "pool"
    rpc_url = "rpc1"
    chain_id_str = "base"


class _BscConfig:
    pool_address = "bsc_pool"
    rpc_url = "rpc1"
    chain_id_str = "bsc"


def test_store_persists_per_pool_block_cursor(tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    now_ms = int(time.time() * 1000)
    store.record(
        "pool",
        Decimal("25"),
        timestamp_ms=now_ms,
        event_id="tx1:0",
        block_number=123,
        allow_unseeded=True,
    )
    store.mark_seeded("pool")

    reloaded = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    reloaded.load()

    assert reloaded.last_seen_block("pool") == 123
    assert reloaded.get_24h_volume_usd("pool") == Decimal("25")


def test_sync_gap_fills_seeded_pool_from_its_own_block_cursor(monkeypatch, tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    store.update_cursor("pool", 500)
    store.mark_seeded("pool")
    monkeypatch.setattr(dex_volume, "_STORE", store)

    captured: dict[str, int | None] = {}

    async def _capture(config, from_block=None):
        captured["from_block"] = from_block

    monkeypatch.setattr(dex_volume, "_refresh_pool", _capture)

    asyncio.run(dex_volume.sync_pool_volume_24h(_SyncConfig()))

    assert captured["from_block"] == 500


def test_sync_gap_anchor_is_per_pool_not_global(monkeypatch, tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    store.update_cursor("pool", 900)
    store.update_cursor("bsc_pool", 300)
    store.mark_seeded("pool")
    store.mark_seeded("bsc_pool")
    monkeypatch.setattr(dex_volume, "_STORE", store)

    captured: dict[str, int | None] = {}

    async def _capture(config, from_block=None):
        captured[config.pool_address] = from_block

    monkeypatch.setattr(dex_volume, "_refresh_pool", _capture)

    asyncio.run(dex_volume.sync_pool_volume_24h(_SyncConfig()))
    asyncio.run(dex_volume.sync_pool_volume_24h(_BscConfig()))

    assert captured["pool"] == 900
    assert captured["bsc_pool"] == 300


def test_seeded_quiet_pool_uses_cursor_instead_of_full_rescan(monkeypatch, tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    store.update_cursor("pool", 777)
    store.mark_seeded("pool")
    monkeypatch.setattr(dex_volume, "_STORE", store)

    captured: dict[str, int | None] = {}

    async def _capture(config, from_block=None):
        captured["from_block"] = from_block

    monkeypatch.setattr(dex_volume, "_refresh_pool", _capture)

    asyncio.run(dex_volume.sync_pool_volume_24h(_SyncConfig()))

    assert store.get_24h_volume_usd("pool") == Decimal("0")
    assert captured["from_block"] == 777


def test_sync_full_scans_unseeded_pool(monkeypatch, tmp_path):
    store = RollingDexVolumeStore(tmp_path / "dex_volume.json")
    monkeypatch.setattr(dex_volume, "_STORE", store)

    captured: dict[str, int | None] = {"from_block": -1}

    async def _capture(config, from_block=None):
        captured["from_block"] = from_block

    monkeypatch.setattr(dex_volume, "_refresh_pool", _capture)

    asyncio.run(dex_volume.sync_pool_volume_24h(_SyncConfig()))

    assert captured["from_block"] is None


def test_resume_scan_clamps_stale_cursor_to_24h_window(monkeypatch):
    captured: dict[str, int] = {}

    class _Eth:
        async def get_block(self, block_id):
            assert block_id == "latest"
            return {"number": 1_000_000, "timestamp": 1_700_000_000}

        async def get_logs(self, params):
            captured.setdefault("fromBlock", params["fromBlock"])
            captured.setdefault("toBlock", params["toBlock"])
            return []

    class _Config:
        chain_id_str = "base"
        pool_manager = "0x" + "11" * 20
        pool_address = "0x" + "22" * 32
        token0_symbol = "cNGN"
        token1_symbol = "USDC"
        token0_decimals = 6
        token1_decimals = 6

    monkeypatch.setattr(dex_volume, "_make_async_w3", lambda config, rpc_url: type("W3", (), {"eth": _Eth()})())
    monkeypatch.setattr(dex_volume.time, "time", lambda: 1_700_000_000)

    state = asyncio.run(_scan_pool_window_from_rpc(_Config(), "rpc1", from_block=100))

    assert captured["fromBlock"] == 956_800
    assert captured["toBlock"] == 958_799
    assert state.last_seen_block == 1_000_000
