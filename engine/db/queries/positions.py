"""Position snapshot queries."""
# lp_token_ids table is created in the migration if not exists block at DB open time.

from __future__ import annotations

import json

import aiosqlite

from engine.types import Position


async def insert_position(conn: aiosqlite.Connection, position: Position) -> None:
    await conn.execute(
        """
        INSERT INTO position_snapshots (
            venue, pair, timestamp_ms, balances_json, lp_position_json,
            position_value_usd, volume_24h_usd, rates_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue, pair, timestamp_ms) DO UPDATE SET
            balances_json = excluded.balances_json,
            lp_position_json = excluded.lp_position_json,
            position_value_usd = excluded.position_value_usd,
            volume_24h_usd = excluded.volume_24h_usd,
            rates_json = excluded.rates_json
        """,
        (
            position.venue,
            position.pair,
            position.timestamp,
            json.dumps({k: float(v) for k, v in position.balances.items()}),
            json.dumps(position.lp_position.model_dump(mode="json")) if position.lp_position else None,
            float(position.position_value_usd) if position.position_value_usd is not None else None,
            float(position.volume_24h_usd) if position.volume_24h_usd is not None else None,
            json.dumps({k: float(v) for k, v in position.rates.items()}) if position.rates else None,
        ),
    )
    await conn.commit()


async def save_lp_token_id(conn: aiosqlite.Connection, venue: str, token_id: int) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO lp_token_ids (venue, token_id) VALUES (?, ?)",
        (venue, token_id),
    )
    await conn.commit()


async def get_lp_token_ids(conn: aiosqlite.Connection, venue: str) -> list[int]:
    cursor = await conn.execute(
        "SELECT token_id FROM lp_token_ids WHERE venue = ?", (venue,)
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def remove_lp_token_id(conn: aiosqlite.Connection, venue: str, token_id: int) -> None:
    await conn.execute(
        "DELETE FROM lp_token_ids WHERE venue = ? AND token_id = ?",
        (venue, token_id),
    )
    await conn.commit()
