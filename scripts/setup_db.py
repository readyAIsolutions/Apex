#!/usr/bin/env python3
"""Idempotent DB bootstrap via asyncpg (Python's own socket = the real cluster).

WARNING (this box): do NOT shell out to `psql` from Python here — subprocess psql is
routed to an isolated postgres sandbox whose changes do not persist. asyncpg connects
directly and persists.

Order: roles -> database -> tracked migrations -> seed keys.
Usage:  .venv/bin/python scripts/setup_db.py
Superuser password from env PGPASSWORD (default postgres).
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))

PGHOST = os.environ.get("PGHOST", "127.0.0.1")
PGPORT = int(os.environ.get("PGPORT", "5432"))
SUPER_USER = os.environ.get("PGUSER", "postgres")
SUPER_PW = os.environ.get("PGPASSWORD", "postgres")
MIG_PW = "apex_migrator_dev_password"
APP_PW = "apex_app_dev_password"
DB = "apex"
TENANT_AIMBEX = "11111111-1111-4111-8111-111111111111"


async def main() -> None:
    import asyncpg

    su = await asyncpg.connect(host=PGHOST, port=PGPORT, user=SUPER_USER,
                               password=SUPER_PW, database="postgres")
    try:
        for role, pw, opts in [
            ("apex_migrator", MIG_PW, "LOGIN CREATEDB BYPASSRLS"),
            ("apex_app", APP_PW, "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"),
        ]:
            if not await su.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role):
                print(f"create role {role}")
                await su.execute(f"CREATE ROLE {role} {opts} PASSWORD '{pw}'")
            else:
                print(f"ensure role {role}")
                await su.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD '{pw}'")

        if not await su.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", DB):
            print(f"create database {DB}")
            await su.execute(f"CREATE DATABASE {DB} OWNER apex_migrator")
        else:
            print(f"database {DB} exists")
    finally:
        await su.close()

    mig = await asyncpg.connect(host=PGHOST, port=PGPORT, user="apex_migrator",
                                password=MIG_PW, database=DB)
    try:
        await mig.execute("CREATE TABLE IF NOT EXISTS apex_migrations "
                          "(file text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        applied = {r["file"] for r in await mig.fetch("SELECT file FROM apex_migrations")}
        for f in MIGRATIONS:
            if f.name in applied:
                print(f"  skip {f.name} (applied)")
                continue
            print(f"  apply {f.name}")
            await mig.execute(f.read_text())              # multi-statement via simple protocol
            await mig.execute("INSERT INTO apex_migrations (file) VALUES ($1)", f.name)

        # seed API keys (BYPASSRLS role ignores row-security policies)
        entries = [("admin", os.environ.get("BOOTSTRAP_ADMIN_KEY",
                                            "apex_dev_admin_key_change_me"), ["admin"])]
        for part in os.environ.get("BOOTSTRAP_KEYS", "").split(","):
            part = part.strip()
            if ":" in part:
                role, key = part.split(":", 1)
                entries.append((f"bootstrap_{role}", key.strip(), [role]))
        for name, key, roles in entries:
            h = hashlib.sha256(key.encode()).hexdigest()
            await mig.execute(
                "INSERT INTO api_keys (tenant_id, name, key_hash, roles) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (key_hash) DO NOTHING", TENANT_AIMBEX, name, h, roles)
            print(f"  seed api_key '{name}' roles={roles}")
    finally:
        await mig.close()

    print("== done. Next: .venv/bin/python scripts/verify.py ==")


if __name__ == "__main__":
    asyncio.run(main())