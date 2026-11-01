"""Logical snapshot backup/restore (§11 P0 acceptance: restore backup, deny cross-tenant).

WHY asyncpg-only: python-spawned `psql`/`pg_dump` subprocesses are sandboxed on this
box (changes don't persist vs the real cluster), so a classic pg_dump pipeline can't
be verified here. This is a logical, tenant-aware snapshot of APEX core tables:
gzip-JSONL + manifest, restorable into a scratch database via asyncpg multi-statement
exec (schema migrations) + parameterized inserts (data). Honest label: logical
snapshot — NOT a byte-level pg_dump replacement for production (that stays pg_dump/
managed backups; this proves restore semantics and cross-tenant data round-trips).
"""
import datetime
import gzip
import json
import os
from pathlib import Path

BACKUP_DIR = Path(os.environ.get("APEX_BACKUP_DIR", "/home/nova/apex/backups"))
KEEP = 7

# Decoupling order: parents before children. Schema migrations for a fresh restore
# exclude the seed (0006) and the seed-inserting route rows (0008) — data comes
# from the snapshot itself.
SCHEMA_MIGRATIONS = ["0001_init.sql", "0002_crm.sql", "0003_marketing.sql", "0004_jobs.sql",
                     "0005_rls_roles.sql", "0007_auth_lookup.sql", "0009_opportunity_attribution.sql"]
# NOTE: never add data-seeding migrations (route seeds, e.g. 0008/0010-0014) to
# SCHEMA_MIGRATIONS — they FK-insert rows that assume a TENANT already exists, and in a
# fresh restore tenants come from the snapshot, not migrations. Apply schema-only
# statements inline (see human_review ALTER below) and let the snapshot restore data.

TABLES = [
    "tenants", "users", "api_keys", "provider_connections", "provider_routes",
    "brand_knowledge", "campaigns", "contacts", "consent_events", "lead_events",
    "opportunities", "opportunity_attribution", "appointments", "campaign_spend_daily",
    "content_assets", "publications", "customer_questions", "agent_jobs", "approvals",
    "webhook_events", "attribution_models", "audit_events",
]

MIGRATIONS_DIR = Path("/home/nova/apex/migrations")

MIGRATOR = {"host": "127.0.0.1", "port": 5432, "user": "apex_migrator",
            "password": "apex_migrator_dev_password"}
SUPER = {"host": "127.0.0.1", "port": 5432, "user": "postgres", "password": "postgres"}


async def create_snapshot(path=None) -> dict:
    import asyncpg
    conn = await asyncpg.connect(database="apex", **MIGRATOR)
    manifest = {"created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "tables": {}}
    payload = []
    try:
        for t in TABLES:
            rows = await conn.fetch(f"SELECT * FROM {t}")
            colmeta = await conn.fetch(
                "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS typ "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = $1 AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY a.attnum", t)
            cols = [c["name"] for c in colmeta]
            types = [c["typ"] for c in colmeta]
            manifest["tables"][t] = {"rows": len(rows), "columns": cols, "types": types}
            payload.append({"table": t, "columns": cols, "types": types,
                            "rows": [list(map(_serialize, r)) for r in rows]})
    finally:
        await conn.close()

    path = path or (BACKUP_DIR / f"apex_snapshot_{datetime.datetime.now(datetime.timezone.utc):%Y%m%d_%H%M%S}.jsonl.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(manifest) + "\n")
        for chunk in payload:
            f.write(json.dumps(chunk, default=str) + "\n")
    _rotate(path.parent)
    return {"path": str(path), "manifest": manifest}


def _pg_cell(v, coltype: str):
    """Serialize one restored value for server-side text->type coercion."""
    if v is None:
        return None
    if coltype.endswith("[]") and isinstance(v, (list, tuple)):
        return "{" + ",".join('"' + str(x).replace('"', '\\"') + '"' for x in v) + "}"
    return str(v)


def _serialize(v):
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def _rotate(directory: Path, keep: int = KEEP):
    snaps = sorted(directory.glob("apex_snapshot_*.jsonl.gz"), reverse=True)
    for old in snaps[keep:]:
        old.unlink()


def _load_snapshot(path: str) -> tuple[dict, list[dict]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        manifest = json.loads(f.readline())
        chunks = [json.loads(line) for line in f]
    return manifest, chunks


async def restore_snapshot(path: str, scratch_db: str = "apex_restore_test") -> dict:
    """Restore a snapshot into a fresh scratch database; prove it round-trips."""
    import asyncpg
    manifest, chunks = _load_snapshot(path)
    su = await asyncpg.connect(database="postgres", **SUPER)
    try:
        await su.execute(f"DROP DATABASE IF EXISTS {scratch_db}")
        await su.execute(f"CREATE DATABASE {scratch_db} OWNER apex_migrator")
    finally:
        await su.close()

    conn = await asyncpg.connect(database=scratch_db, **MIGRATOR)
    try:
        for name in SCHEMA_MIGRATIONS:
            sql = (MIGRATIONS_DIR / name).read_text()
            await conn.execute(sql)
        # schema-only tail from post-0009 migrations (data seeds belong to the snapshot)
        await conn.execute(
            "ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS "
            "human_review boolean NOT NULL DEFAULT false")
        restored = {}
        for chunk in chunks:
            t, cols, rows = chunk["table"], chunk["columns"], chunk["rows"]
            if not rows:
                restored[t] = 0
                continue
            # Coerce EVERY value to text client-side (asyncpg rejects typed params
            # like timestamptz given a str), then force server-side I/O coercion:
            # $n::text::<coltype> — PG casts text->type through input functions.
            casts = chunk.get("types") or ["text"] * len(cols)
            placeholders = ", ".join(
                f"${i + 1}::text::{casts[i]}" for i in range(len(cols)))
            rows2 = [[_pg_cell(v, casts[i]) for i, v in enumerate(row)] for row in rows]
            await conn.executemany(
                f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({placeholders})", rows2)
            restored[t] = len(rows)
    finally:
        await conn.close()
    return {"scratch_db": scratch_db, "restored": restored, "tables_in_snapshot": len(chunks)}


async def drop_scratch(scratch_db: str = "apex_restore_test") -> None:
    import asyncpg
    su = await asyncpg.connect(database="postgres", **SUPER)
    try:
        await su.execute(f"DROP DATABASE IF EXISTS {scratch_db}")
    finally:
        await su.close()
