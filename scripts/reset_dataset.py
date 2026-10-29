"""Utility script to reset and flush the ChorusRank dataset cleanly.

Wipes:
- MongoDB collections: 'events', 'profiles', 'status_history' in ChorusRank database
- Local fallback files: data/raw/*.parquet, data/enriched/*.parquet, data/processed/*.parquet, data/chorusrank.duckdb
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chorusrank.storage import get_storage
from chorusrank.storage.mongo import MongoStorage


def reset_local_storage(data_dir: Path = Path("data")) -> None:
    """Remove all generated local parquet and duckdb files."""
    count = 0
    if not data_dir.exists():
        return

    for p in data_dir.glob("**/*"):
        if p.is_file() and not p.name.startswith(".gitkeep"):
            try:
                p.unlink()
                count += 1
            except Exception as e:
                print(f"[Reset] Warning: Could not remove {p}: {e}")

    print(f"[Reset] Removed {count} local data files from {data_dir.resolve()}.")


def reset_mongo_storage(storage: MongoStorage) -> None:
    """Drop or clear all documents in ChorusRank MongoDB collections."""
    db = storage.db
    cols = ["events", "profiles", "status_history"]
    for col_name in cols:
        col = db[col_name]
        deleted = col.delete_many({}).deleted_count
        print(f"[Reset] MongoDB: Cleared {deleted} documents from collection '{col_name}'.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Flush and restart ChorusRank dataset")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag to confirm dataset deletion",
    )
    args = parser.parse_args()

    if not args.confirm:
        print("=" * 60)
        print("CAUTION: This will delete ALL collected posts, profiles, and status history!")
        print("To proceed, run with --confirm:")
        print("  uv run python scripts/reset_dataset.py --confirm")
        print("=" * 60)
        sys.exit(1)

    print("[Reset] Starting dataset flush...")

    # 1. Reset local files
    reset_local_storage()

    # 2. Reset MongoDB if accessible
    storage = get_storage()
    if isinstance(storage, MongoStorage):
        print(f"[Reset] Connected to MongoDB Atlas database: '{storage.db_name}'")
        reset_mongo_storage(storage)
    else:
        print("[Reset] Primary storage is currently local Parquet/DuckDB (cleared).")

    print("\n[Reset] SUCCESS: Dataset has been completely flushed and reset.")
    print("[Reset] You can now start a fresh collection.")


if __name__ == "__main__":
    main()
