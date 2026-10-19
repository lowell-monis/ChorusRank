"""Storage module for ChorusRank with automatic backend selection."""

from __future__ import annotations

import os
from typing import Union
from dotenv import load_dotenv

from .schema import RawEventRecord, ProfileMetadata, AccountStatusRecord
from .db import ChorusStorage
from .mongo import MongoStorage

load_dotenv()


def get_storage(backend: str | None = None) -> Union[MongoStorage, ChorusStorage]:
    """Factory function returning the configured storage backend.
    
    Defaults to MongoStorage if MONGO_URI is set or backend='mongodb'.
    Falls back to Parquet/DuckDB (ChorusStorage) if backend='parquet'.
    """
    selected = (backend or os.getenv("STORAGE_BACKEND", "mongodb")).lower()

    if selected == "mongodb" or (selected != "parquet" and os.getenv("MONGO_URI")):
        try:
            return MongoStorage()
        except Exception as e:
            print(f"[Storage Factory] Warning: Failed to connect to MongoDB ({e}). Falling back to local Parquet storage.")
            return ChorusStorage()

    return ChorusStorage()


__all__ = [
    "RawEventRecord",
    "ProfileMetadata",
    "AccountStatusRecord",
    "ChorusStorage",
    "MongoStorage",
    "get_storage",
]
