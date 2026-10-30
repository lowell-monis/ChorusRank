"""NoSQL MongoDB storage backend for ChorusRank."""

from __future__ import annotations

import os
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

import certifi
from pymongo import MongoClient, UpdateOne, InsertOne
from pymongo.errors import BulkWriteError
from dotenv import load_dotenv

from .schema import RawEventRecord, ProfileMetadata, AccountStatusRecord

load_dotenv()


def compute_text_similarity(t1: str, t2: str) -> float:
    """Compute text similarity between two post texts using token Jaccard and character 3-grams.
    
    Returns a float between 0.0 (completely dissimilar) and 1.0 (identical).
    """
    if not t1 and not t2:
        return 1.0
    if not t1 or not t2:
        return 0.0
    if t1 == t2:
        return 1.0

    # 1. Alphanumeric token Jaccard
    tokens1 = set(re.findall(r"\w+", t1.lower()))
    tokens2 = set(re.findall(r"\w+", t2.lower()))
    if not tokens1 and not tokens2:
        return 1.0

    jaccard = len(tokens1 & tokens2) / max(1, len(tokens1 | tokens2))

    # 2. Character 3-gram similarity (detects minor typo fixes or punctuation edits in short posts)
    if len(t1) >= 3 and len(t2) >= 3:
        shingles1 = {t1[i:i+3].lower() for i in range(len(t1) - 2)}
        shingles2 = {t2[i:i+3].lower() for i in range(len(t2) - 2)}
        shingle_jaccard = len(shingles1 & shingles2) / max(1, len(shingles1 | shingles2))
        return max(jaccard, shingle_jaccard)

    return jaccard


class MongoStorage:
    """Manages raw events, profile metadata, and longitudinal status history in MongoDB."""

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        db_name: Optional[str] = None,
        cert_file_path: Optional[str] = None,
    ):
        self.mongo_uri = mongo_uri or os.getenv("MONGO_URI") or "mongodb://localhost:27017"
        self.db_name = db_name or os.getenv("MONGO_DB_NAME") or "ChorusRank"
        self.cert_file_path = cert_file_path or os.getenv("CERT_FILE_PATH", "")

        client_kwargs: Dict[str, Any] = {"serverSelectionTimeoutMS": 4000}
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
            self.events_col.create_index("post_uri")
            self.events_col.create_index("is_deleted")
            self.events_col.create_index("domains")
            self.events_col.create_index("tags")
            self.events_col.create_index("mentions")
            self.events_col.create_index("reply_parent_did")
            self.events_col.create_index("quote_did")

            # Profiles index
            self.profiles_col.create_index("did", unique=True)
            self.profiles_col.create_index("followers_count")
            self.profiles_col.create_index("has_moderation_labels")
            self.profiles_col.create_index("label_flag_suspend")
            self.profiles_col.create_index("label_flag_spam")

            # Status history index
            self.status_col.create_index([("did", 1), ("checked_at", -1)])
            self.status_col.create_index("status")
            self.status_col.create_index("label_strict_takedown")
            self.status_col.create_index("label_broad_inauthentic")
            self.status_col.create_index("label_category")
        except Exception as e:
            print(f"[MongoStorage] Note on index creation: {e}")

    def save_raw_events(
        self,
        events: List[RawEventRecord],
        similarity_threshold: float = 0.25,
        **kwargs,
    ) -> int:
        """Save a batch of raw events with text-similarity verification and collision defense.

        Deduplication Algorithm:
        1. Query DB for existing documents matching event_ids in this batch.
        2. If event_id is NEW:
           - Insert cleanly with all fields and initial matched_queries.
        3. If event_id MATCHES an existing document:
           - Compare incoming text with existing DB text:
             a. EXACT / HIGH SIMILARITY (sim >= 0.95):
                - Same post re-encountered: refresh dynamic engagement metrics (likes, reposts, labels)
                  and append query to matched_queries ($addToSet). Preserve original text and timestamps.
             b. SOMEWHAT SIMILAR (similarity_threshold <= sim < 0.95):
                - EDITED POST: Author edited post text. Update text, clean_text, NLP word/char counts,
                  entities, and mark is_edited=True and previous_text=existing_text, while also
                  updating engagement metrics and accumulating matched_queries.
             c. COMPLETELY DIFFERENT TEXT (sim < similarity_threshold):
                - COLLISION EDGE CASE: Two completely different posts accidentally produced the same ID.
                  DO NOT overwrite the original post and DO NOT update its metrics.
                  Disambiguate the new post with a collision key and insert it independently,
                  protecting the original post and preserving both records.
        """
        if not events:
            return 0

        valid_events = [e for e in events if e.event_id and e.did]
        if not valid_events:
            return 0

        incoming_ids = [e.event_id for e in valid_events]
        existing_docs: Dict[str, dict] = {
            doc["event_id"]: doc
            for doc in self.events_col.find(
                {"event_id": {"$in": incoming_ids}},
                {"event_id": 1, "text": 1, "clean_text": 1, "created_at": 1}
            )
        }

        operations = []
        for e in valid_events:
            existing = existing_docs.get(e.event_id)

            if existing is None:
                # Brand new post -> insert cleanly
                doc = e.model_dump()
                if e.query_tag and not doc.get("matched_queries"):
                    doc["matched_queries"] = [e.query_tag]
                operations.append(InsertOne(doc))
                existing_docs[e.event_id] = doc

            else:
                existing_text = existing.get("text", "")
                sim = compute_text_similarity(e.text, existing_text)

                if sim >= 0.95:
                    # Same post re-encountered: refresh metrics, accumulate query tags
                    dynamic_updates = {
                        "like_count": e.like_count,
                        "repost_count": e.repost_count,
                        "reply_count": e.reply_count,
                        "quote_count": e.quote_count,
                        "is_deleted": e.is_deleted,
                        "last_checked_at": e.last_checked_at,
                        "post_labels": e.post_labels,
                        "post_label_values": e.post_label_values,
                        "author_labels": e.author_labels,
                        "author_label_values": e.author_label_values,
                    }
                    update_doc: Dict[str, Any] = {"$set": dynamic_updates}
                    if e.query_tag:
                        update_doc["$addToSet"] = {"matched_queries": e.query_tag}

                    operations.append(
                        UpdateOne({"event_id": e.event_id}, update_doc)
                    )

                elif sim >= similarity_threshold:
                    # Post was EDITED! Update text to edited version AND refresh metrics
                    edited_updates = {
                        "text": e.text,
                        "clean_text": e.clean_text,
                        "langs": e.langs,
                        "urls": e.urls,
                        "domains": e.domains,
                        "tags": e.tags,
                        "mentions": e.mentions,
                        "char_length": e.char_length,
                        "word_count": e.word_count,
                        "url_count": e.url_count,
                        "domain_count": e.domain_count,
                        "tag_count": e.tag_count,
                        "mention_count": e.mention_count,
                        "facets": e.facets,
                        "embed": e.embed,
                        "is_edited": True,
                        "previous_text": existing_text,
                        "like_count": e.like_count,
                        "repost_count": e.repost_count,
                        "reply_count": e.reply_count,
                        "quote_count": e.quote_count,
                        "is_deleted": e.is_deleted,
                        "last_checked_at": e.last_checked_at,
                        "post_labels": e.post_labels,
                        "post_label_values": e.post_label_values,
                        "author_labels": e.author_labels,
                        "author_label_values": e.author_label_values,
                    }
                    update_doc = {"$set": edited_updates}
                    if e.query_tag:
                        update_doc["$addToSet"] = {"matched_queries": e.query_tag}

                    operations.append(
                        UpdateOne({"event_id": e.event_id}, update_doc)
                    )

                else:
                    # COLLISION EDGE CASE: Completely different text under the same ID
                    # Protect original post; save incoming post under disambiguated collision ID
                    collision_hash = hashlib.sha256((e.text or "").encode("utf-8")).hexdigest()[:8]
                    disambiguated_id = f"{e.event_id}_collision_{collision_hash}"

                    collided_doc = e.model_dump()
                    collided_doc["event_id"] = disambiguated_id
                    if e.query_tag and not collided_doc.get("matched_queries"):
                        collided_doc["matched_queries"] = [e.query_tag]

                    print(
                        f"[MongoStorage] Collision detected for ID '{e.event_id}': "
                        f"text similarity ({sim:.2f}) < {similarity_threshold}. "
                        f"Saved incoming post as '{disambiguated_id}' to protect original post."
                    )
                    operations.append(InsertOne(collided_doc))

        if not operations:
            return 0

        try:
            res = self.events_col.bulk_write(operations, ordered=False)
            inserted_or_upserted = (res.upserted_count or 0) + (res.modified_count or 0) + (res.inserted_count or 0)
            return inserted_or_upserted
        except BulkWriteError as bwe:
            return bwe.details.get("nInserted", 0) + bwe.details.get("nUpserted", 0)

    def save_profiles(self, profiles: List[ProfileMetadata], **kwargs) -> int:
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

    def save_status_checks(self, records: List[AccountStatusRecord], **kwargs) -> int:
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

    def get_post_uris(self, sample_limit: Optional[int] = None) -> List[str]:
        """Fetch distinct post URIs for original posts that are not marked as deleted."""
        query = {"collection": "app.bsky.feed.post", "post_uri": {"$ne": None}, "is_deleted": {"$ne": True}}
        cursor = self.events_col.find(query, {"post_uri": 1})
        if sample_limit:
            cursor = cursor.limit(sample_limit)
        return [doc["post_uri"] for doc in cursor if "post_uri" in doc]

    def update_post_metrics(self, updates: List[Dict[str, Any]]) -> int:
        """Batch update like/repost counts and deletion status for posts."""
        if not updates:
            return 0
        operations = []
        for u in updates:
            post_uri = u.get("post_uri")
            if not post_uri:
                continue
            update_data = {
                "like_count": u.get("like_count", 0),
                "repost_count": u.get("repost_count", 0),
                "reply_count": u.get("reply_count", 0),
                "quote_count": u.get("quote_count", 0),
                "is_deleted": u.get("is_deleted", False),
                "last_checked_at": u.get("last_checked_at"),
            }
            operations.append(
                UpdateOne({"post_uri": post_uri}, {"$set": update_data})
            )
        try:
            res = self.events_col.bulk_write(operations, ordered=False)
            return (res.modified_count or 0) + (res.upserted_count or 0)
        except BulkWriteError as bwe:
            return bwe.details.get("nModified", 0)

    def get_dataset_summary(self) -> Dict[str, Any]:
        """Generate comprehensive dataset metrics across all collections."""
        total_events = self.events_col.estimated_document_count()
        unique_dids = len(self.get_distinct_dids())
        profiles_count = self.profiles_col.estimated_document_count()
        labeled_profiles_count = self.profiles_col.count_documents({"has_moderation_labels": True})
        
        strict_takedowns = self.status_col.count_documents({"label_strict_takedown": True})
        broad_inauthentic = self.status_col.count_documents({"label_broad_inauthentic": True})
        deleted_posts_count = self.events_col.count_documents({"is_deleted": True})

        earliest_doc = self.events_col.find_one(sort=[("created_at", 1)])
        latest_doc = self.events_col.find_one(sort=[("created_at", -1)])

        posts_count = self.events_col.count_documents({"collection": "app.bsky.feed.post"})
        distinct_domains = len(self.events_col.distinct("domains"))
        distinct_tags = len(self.events_col.distinct("tags"))

        return {
            "storage_backend": "MongoDB Atlas (NoSQL)",
            "database_name": self.db_name,
            "total_events": total_events,
            "posts_count": posts_count,
            "unique_dids": unique_dids,
            "profiles_enriched": profiles_count,
            "profiles_with_moderation_labels": labeled_profiles_count,
            "strict_takedowns_detected": strict_takedowns,
            "broad_inauthentic_detected": broad_inauthentic,
            "deleted_posts_detected": deleted_posts_count,
            "distinct_external_domains": distinct_domains,
            "distinct_hashtags": distinct_tags,
            "earliest_event": earliest_doc.get("created_at") if earliest_doc else None,
            "latest_event": latest_doc.get("created_at") if latest_doc else None,
        }
