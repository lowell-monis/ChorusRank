"""Storage module for ChorusRank with automatic backend selection."""

from __future__ import annotations

import os
from typing import Union
from dotenv import load_dotenv

from .schema import RawEventRecord, ProfileMetadata, AccountStatusRecord, GoldLabelRecord
from .db import ChorusStorage
from .mongo import MongoStorage

load_dotenv()


def get_storage(backend: str | None = None) -> Union[MongoStorage, ChorusStorage]:
    """Factory function returning the configured storage backend.
    
    Defaults to MongoStorage if MONGO_URI is set or backend='mongodb'.
    Falls back to Parquet/DuckDB (ChorusStorage) if backend='parquet' or if
    MongoDB network access fails (e.g. IP not whitelisted in Atlas).
    """
    selected = (backend or os.getenv("STORAGE_BACKEND", "mongodb")).lower()

    if selected == "mongodb" or (selected != "parquet" and os.getenv("MONGO_URI")):
        uri = os.getenv("MONGO_URI", "").strip()
        if not uri and selected == "mongodb":
            print("\n[Storage Factory] ERROR: STORAGE_BACKEND is set to 'mongodb' but MONGO_URI is empty!")
            print("[Storage Factory] Ensure 'MONGO_URI' secret is configured in GitHub Repository Settings -> Secrets -> Actions.\n")
            return ChorusStorage()

        try:
            storage = MongoStorage()
            # Fast ping with 4s timeout to verify network and credentials
            storage.client.admin.command("ping")
            return storage
        except Exception as e:
            print(f"\n[Storage Factory] MongoDB Atlas ping failed: {e}")
            print("[Storage Factory] Note: For GitHub Actions runners, ensure 0.0.0.0/0 is whitelisted in MongoDB Atlas -> Network Access.\n")
            return ChorusStorage()

    return ChorusStorage()


__all__ = [
    "RawEventRecord",
    "ProfileMetadata",
    "AccountStatusRecord",
    "GoldLabelRecord",
    "ChorusStorage",
    "MongoStorage",
    "get_storage",
]
