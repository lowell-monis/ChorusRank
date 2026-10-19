"""NoSQL MongoDB storage backend for ChorusRank."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

import certifi
from pymongo import MongoClient, UpdateOne, InsertOne
from pymongo.errors import BulkWriteError
from dotenv import load_dotenv

from .schema import RawEventRecord, ProfileMetadata, AccountStatusRecord

load_dotenv()


class MongoStorage:
    """Manages raw events, profile metadata, and longitudinal status history in MongoDB."""

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        db_name: Optional[str] = None,
        cert_file_path: Optional[str] = None,
    ):
        self.mongo_uri = mongo_uri or os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.db_name = db_name or os.getenv("MONGO_DB_NAME", "chorusrank")
        self.cert_file_path = cert_file_path or os.getenv("CERT_FILE_PATH", "")

        client_kwargs: Dict[str, Any] = {}

        if "mongodb+srv://" in self.mongo_uri or "authMechanism=MONGODB-X509" in self.mongo_uri:
            client_kwargs["tls"] = True
            client_kwargs["tlsCAFile"] = certifi.where()
            if self.cert_file_path and Path(self.cert_file_path).exists():
                client_kwargs["tlsCertificateKeyFile"] = self.cert_file_path

        self.client = MongoClient(self.mongo_uri, **client_kwargs)
        self.db = self.client[self.db_name]

        # Collections
        self.events_col = self.db["events"]
        self.profiles_col = self.db["profiles"]
        self.status_col = self.db["status_history"]

        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        """Create necessary performance and uniqueness indexes."""
        try:
            # Events indexes
            self.events_col.create_index("event_id", unique=True)
            self.events_col.create_index("did")
            self.events_col.create_index("created_at")
            self.events_col.create_index("collection")
            self.events_col.create_index("query_tag")

            # Profiles index
            self.profiles_col.create_index("did", unique=True)
            self.profiles_col.create_index("followers_count")

            # Status history index
            self.status_col.create_index([("did", 1), ("checked_at", -1)])
            self.status_col.create_index("status")
        except Exception as e:
            print(f"[MongoStorage] Note on index creation: {e}")

    def save_raw_events(self, events: List[RawEventRecord]) -> int:
        """Save a batch of raw events using upsert to avoid duplicate keys."""
        if not events:
            return 0

        operations = []
        for e in events:
            doc = e.model_dump()
            operations.append(
                UpdateOne(
                    {"event_id": e.event_id},
                    {"$set": doc},
                    upsert=True,
                )
            )

        try:
            res = self.events_col.bulk_write(operations, ordered=False)
            inserted_or_upserted = (res.upserted_count or 0) + (res.modified_count or 0)
            return inserted_or_upserted
        except BulkWriteError as bwe:
            # Duplicate keys are handled gracefully by ordered=False
            return bwe.details.get("nUpserted", 0)

    def save_profiles(self, profiles: List[ProfileMetadata]) -> int:
        """Upsert enriched profile records by DID."""
        if not profiles:
            return 0

        operations = []
        for p in profiles:
            doc = p.model_dump()
            operations.append(
                UpdateOne(
                    {"did": p.did},
                    {"$set": doc},
                    upsert=True,
                )
            )

        try:
            res = self.profiles_col.bulk_write(operations, ordered=False)
            return (res.upserted_count or 0) + (res.modified_count or 0)
        except BulkWriteError as bwe:
            return bwe.details.get("nUpserted", 0)

    def save_status_checks(self, records: List[AccountStatusRecord]) -> int:
        """Insert longitudinal status checks."""
        if not records:
            return 0

        docs = [r.model_dump() for r in records]
        res = self.status_col.insert_many(docs, ordered=False)
        return len(res.inserted_ids)

    def get_distinct_dids(self) -> List[str]:
        """Fetch all unique DIDs observed across stored raw events."""
        return self.events_col.distinct("did")

    def get_already_enriched_dids(self) -> Set[str]:
        """Fetch all DIDs that already have profile metadata."""
        return set(self.profiles_col.distinct("did"))

    def get_dataset_summary(self) -> Dict[str, Any]:
        """Generate high-level dataset metrics for the intermediate report."""
        total_events = self.events_col.estimated_document_count()
        unique_dids = len(self.get_distinct_dids())
        profiles_count = self.profiles_col.estimated_document_count()
        takedowns_count = self.status_col.count_documents({"status": {"$in": ["TAKEDOWN", "DEACTIVATED"]}})

        earliest_doc = self.events_col.find_one(sort=[("created_at", 1)])
        latest_doc = self.events_col.find_one(sort=[("created_at", -1)])

        posts_count = self.events_col.count_documents({"collection": "app.bsky.feed.post"})
        reposts_count = self.events_col.count_documents({"collection": "app.bsky.feed.repost"})

        return {
            "storage_backend": "MongoDB (NoSQL)",
            "database_name": self.db_name,
            "total_events": total_events,
            "posts_count": posts_count,
            "reposts_count": reposts_count,
            "unique_dids": unique_dids,
            "profiles_enriched": profiles_count,
            "takedowns_detected": takedowns_count,
            "earliest_event": earliest_doc.get("created_at") if earliest_doc else None,
            "latest_event": latest_doc.get("created_at") if latest_doc else None,
        }
