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
        try:
            storage = MongoStorage()
            # Fast ping with 3s timeout to verify network and credentials
            storage.client.admin.command("ping")
            return storage
        except Exception as e:
            print(f"[Storage Factory] Notice: MongoDB Atlas ping failed: {e}")
            print("[Storage Factory] Note: If on dynamic/campus WiFi, ensure 0.0.0.0/0 is added in MongoDB Atlas -> Network Access.")
            print("[Storage Factory] Falling back to local Parquet/DuckDB storage for this run.")
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
