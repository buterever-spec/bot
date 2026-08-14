"""
db.py — Single place for all database access.
"""
import os
import asyncpg
import json

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
        await _init_tables(_pool)
    return _pool


async def _init_tables(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS giveaways (
                id         TEXT PRIMARY KEY,
                data       JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alt_products (
                id    TEXT PRIMARY KEY,
                data  JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alt_stock (
                product_id TEXT NOT NULL,
                account    TEXT NOT NULL,
                PRIMARY KEY (product_id, account)
            );
            CREATE TABLE IF NOT EXISTS alt_pending (
                discord_id TEXT PRIMARY KEY,
                data       JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alt_purchases (
                id SERIAL PRIMARY KEY,
                product_id TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                roblox_username TEXT NOT NULL,
                roblox_id TEXT NOT NULL,
                discord_tag TEXT,
                account TEXT,
                status TEXT DEFAULT 'pending',
                purchase_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)


_CONFIG_DEFAULTS = {
    "ticket_count": "0",
    "staff_roles": "[]",
    "ticket_category_id": "",
    "log_channel_id": "",
    "welcome_enabled": "true",
    "welcome_channel_id": "",
    "welcome_message": "",
}


async def get_config():
    pool = await get_pool()
    rows = await pool.fetch("SELECT key, value FROM config")
    cfg = dict(_CONFIG_DEFAULTS)
    for r in rows:
        cfg[r["key"]] = r["value"]
    return {
        "ticket_count": int(cfg["ticket_count"]),
        "staff_roles": json.loads(cfg["staff_roles"]),
        "ticket_category_id": cfg["ticket_category_id"] or None,
        "log_channel_id": cfg["log_channel_id"] or None,
        "welcome_enabled": cfg["welcome_enabled"] == "true",
        "welcome_channel_id": cfg["welcome_channel_id"] or None,
        "welcome_message": cfg["welcome_message"],
    }


async def set_config_key(key, value):
    if isinstance(value, (list, dict)):
        v = json.dumps(value)
    elif isinstance(value, bool):
        v = "true" if value else "false"
    else:
        v = str(value)
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO config (key, value) VALUES ($1, $2)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, key, v)


async def increment_ticket_count():
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO config (key, value) VALUES ('ticket_count', '1')
        ON CONFLICT (key) DO UPDATE SET value = (CAST(config.value AS INT) + 1)::TEXT
    """)
    row = await pool.fetchrow("SELECT value FROM config WHERE key = 'ticket_count'")
    return int(row["value"])


async def load_giveaways():
    pool = await get_pool()
    rows = await pool.fetch("SELECT id, data FROM giveaways")
    return {r["id"]: dict(r["data"]) for r in rows}


async def save_giveaway(gid, data):
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO giveaways (id, data) VALUES ($1, $2::jsonb)
        ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data
    """, gid, json.dumps(data))


async def delete_giveaway(gid):
    pool = await get_pool()
    await pool.execute("DELETE FROM giveaways WHERE id = $1", gid)


async def load_products():
    """Load all products as a list of dicts."""
    pool = await get_pool()
    rows = await pool.fetch("SELECT data FROM alt_products ORDER BY data->>'title'")
    products = []
    for r in rows:
        data = r["data"]
        if isinstance(data, dict):
            products.append(data)
        elif isinstance(data, str):
            try:
                products.append(json.loads(data))
            except:
                products.append({})
        else:
            products.append({})
    return products


async def save_product(product):
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO alt_products (id, data) VALUES ($1, $2::jsonb)
        ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data
    """, product["id"], json.dumps(product))


async def delete_product(product_id):
    pool = await get_pool()
    await pool.execute("DELETE FROM alt_products WHERE id = $1", product_id)
    await pool.execute("DELETE FROM alt_stock WHERE product_id = $1", product_id)


async def load_stock():
    pool = await get_pool()
    rows = await pool.fetch("SELECT product_id, account FROM alt_stock")
    result = {}
    for r in rows:
        result.setdefault(r["product_id"], []).append(r["account"])
    return result


async def add_stock(product_id, accounts):
    pool = await get_pool()
    await pool.executemany(
        "INSERT INTO alt_stock (product_id, account) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        [(product_id, a) for a in accounts],
    )


async def pop_stock(product_id):
    pool = await get_pool()
    row = await pool.fetchrow(
        "DELETE FROM alt_stock WHERE (product_id, account) = "
        "(SELECT product_id, account FROM alt_stock WHERE product_id = $1 LIMIT 1) "
        "RETURNING account",
        product_id,
    )
    return row["account"] if row else None


async def count_stock(product_id):
    pool = await get_pool()
    row = await pool.fetchrow("SELECT COUNT(*) AS n FROM alt_stock WHERE product_id = $1", product_id)
    return row["n"]


async def load_pending():
    pool = await get_pool()
    rows = await pool.fetch("SELECT discord_id, data FROM alt_pending")
    return {r["discord_id"]: dict(r["data"]) for r in rows}


async def save_pending_entry(discord_id, data):
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO alt_pending (discord_id, data) VALUES ($1, $2::jsonb)
        ON CONFLICT (discord_id) DO UPDATE SET data = EXCLUDED.data
    """, discord_id, json.dumps(data))


async def delete_pending_entry(discord_id):
    pool = await get_pool()
    await pool.execute("DELETE FROM alt_pending WHERE discord_id = $1", discord_id)


async def get_pending_entry(discord_id):
    pool = await get_pool()
    row = await pool.fetchrow("SELECT data FROM alt_pending WHERE discord_id = $1", discord_id)
    return dict(row["data"]) if row else None


# -------- Alt Shop Extended --------
async def create_purchase(product_id, discord_id, roblox_username, roblox_id, discord_tag=None):
    pool = await get_pool()
    row = await pool.fetchrow(
        """INSERT INTO alt_purchases 
           (product_id, discord_id, roblox_username, roblox_id, discord_tag, status)
           VALUES ($1, $2, $3, $4, $5, 'pending')
           RETURNING id""",
        product_id, discord_id, roblox_username, roblox_id, discord_tag
    )
    return row["id"]


async def update_purchase_status(purchase_id, status, account=None):
    pool = await get_pool()
    if account:
        await pool.execute(
            "UPDATE alt_purchases SET status = $1, account = $2 WHERE id = $3",
            status, account, purchase_id
        )
    else:
        await pool.execute(
            "UPDATE alt_purchases SET status = $1 WHERE id = $2",
            status, purchase_id
        )


async def get_pending_purchase(purchase_id):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM alt_purchases WHERE id = $1 AND status = 'pending'",
        purchase_id
    )
    return dict(row) if row else None


async def get_purchase_logs(limit=50):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM alt_purchases ORDER BY purchase_time DESC LIMIT $1",
        limit
    )
    return [dict(r) for r in rows]


# ---------- Log channel ----------
async def get_log_channel():
    pool = await get_pool()
    row = await pool.fetchrow("SELECT value FROM config WHERE key = 'alt_log_channel'")
    return int(row["value"]) if row else None


async def set_log_channel(channel_id):
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO config (key, value) VALUES ('alt_log_channel', $1) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        str(channel_id)
    )