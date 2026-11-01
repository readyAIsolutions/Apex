#!/usr/bin/env python3
"""APEX logical snapshot backup CLI (§11 P0). Usage: .venv/bin/python scripts/backup.py"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MIGRATOR_URL", "postgresql://apex_migrator:apex_migrator_dev_password@127.0.0.1:5432/apex")


async def main() -> int:
    from app.services.backup import create_snapshot
    result = await create_snapshot()
    print(f"snapshot written: {result['path']}")
    for t, info in result["manifest"]["tables"].items():
        if info["rows"]:
            print(f"  {t}: {info['rows']} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
