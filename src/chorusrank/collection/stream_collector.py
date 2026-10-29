"""Async Bluesky Jetstream firehose collector with deterministic DID sampling."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional

import websockets

from .api_collector import clean_post_text, extract_entities_and_edges
from ..storage.schema import RawEventRecord
from ..storage import get_storage


class JetstreamStreamCollector:
    """Streams live posts and reposts from Bluesky Jetstream with deterministic sampling."""

    def __init__(
        self,
        jetstream_uri: str = "wss://jetstream2.us-east.bsky.network/subscribe",
        wanted_collections: Optional[List[str]] = None,
        sample_modulo: int = 1,  # 1 = 100% of events, 10 = 10% sampling by DID hash
        batch_flush_size: int = 1000,
        storage=None,
    ):
        self.base_uri = jetstream_uri
        self.wanted_collections = wanted_collections or [
            "app.bsky.feed.post",
            "app.bsky.feed.repost",
        ]
        self.sample_modulo = max(1, sample_modulo)
        self.batch_flush_size = batch_flush_size
        self.storage = storage or get_storage()
        self.running = False

    def _should_sample(self, did: str) -> bool:
        """Deterministically determine if a DID should be sampled."""
        if self.sample_modulo <= 1:
            return True
        hash_val = int(hashlib.md5(did.encode("utf-8")).hexdigest()[:8], 16)
        return (hash_val % self.sample_modulo) == 0

    def _build_ws_url(self, cursor_us: Optional[int] = None) -> str:
        """Construct WebSocket subscription URL with collection filters."""
        query_params = [f"wantedCollections={col}" for col in self.wanted_collections]
        if cursor_us:
            query_params.append(f"cursor={cursor_us}")
        return f"{self.base_uri}?{'&'.join(query_params)}"

    async def stream_indefinitely(self, max_events: Optional[int] = None) -> None:
        """Connect to Jetstream and stream events indefinitely or up to max_events."""
        self.running = True
        event_buffer: List[RawEventRecord] = []
        total_processed = 0
        last_cursor_us: Optional[int] = None

        print(f"[Jetstream Streamer] Subscribing to: {self.wanted_collections}")
        print(f"[Jetstream Streamer] Sampling: 1 out of every {self.sample_modulo} accounts ({100/self.sample_modulo:.1f}%)")

        while self.running:
            url = self._build_ws_url(last_cursor_us)
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print("[Jetstream Streamer] WebSocket connection established. Ingesting...")
                    async for raw_message in ws:
                        if not self.running:
                            break

                        try:
                            msg = json.loads(raw_message)
                        except json.JSONDecodeError:
                            continue

                        last_cursor_us = msg.get("time_us")
                        commit = msg.get("commit", {})
                        collection = commit.get("collection", "")
                        did = msg.get("did", "")

                        if not did or not self._should_sample(did):
                            continue

                        rkey = commit.get("rkey", "")
                        record = commit.get("record", {}) or {}
                        now_iso = datetime.now(timezone.utc).isoformat()

                        # Parse reply or repost pointers
                        reply = record.get("reply", {}) or {}
                        parent_uri = reply.get("parent", {}).get("uri")
                        root_uri = reply.get("root", {}).get("uri")
                        parent_did = parent_uri.split("/")[2] if parent_uri and parent_uri.startswith("at://") else None
                        root_did = root_uri.split("/")[2] if root_uri and root_uri.startswith("at://") else None

                        reposted_uri = None
                        reposted_did = None
                        if collection == "app.bsky.feed.repost":
                            subject = record.get("subject", {}) or {}
                            reposted_uri = subject.get("uri")
                            if reposted_uri and reposted_uri.startswith("at://"):
                                parts = reposted_uri.split("/")
                                if len(parts) >= 3:
                                    reposted_did = parts[2]

                        post_text = record.get("text", "")
                        embed = record.get("embed")
                        extracted = extract_entities_and_edges(record, embed)

                        event_rec = RawEventRecord(
                            event_id=f"{did}_{rkey}",
                            did=did,
                            rkey=rkey,
                            cid=commit.get("cid"),
                            created_at=record.get("createdAt", now_iso),
                            collected_at=now_iso,
                            collection=collection,
                            text=post_text,
                            clean_text=clean_post_text(post_text),
                            langs=record.get("langs", []),
                            urls=extracted["urls"],
                            domains=extracted["domains"],
                            tags=extracted["tags"],
                            mentions=extracted["mentions"],
                            reply_parent_uri=parent_uri,
                            reply_parent_did=parent_did,
                            reply_root_uri=root_uri,
                            reply_root_did=root_did,
                            quote_uri=extracted["quote_uri"],
                            quote_did=extracted["quote_did"],
                            reposted_uri=reposted_uri,
                            reposted_did=reposted_did,
                            facets=record.get("facets", []),
                            embed=embed,
                            stratum="baseline",
                            query_tag="jetstream_sampled",
                            post_uri=f"at://{did}/{collection}/{rkey}" if collection == "app.bsky.feed.post" else None,
                            like_count=0,
                            repost_count=0,
                            reply_count=0,
                            quote_count=0,
                            is_deleted=False,
                            last_checked_at=now_iso,
                            raw_payload=msg,
                        )

                        event_buffer.append(event_rec)
                        total_processed += 1

                        if len(event_buffer) >= self.batch_flush_size:
                            res = self.storage.save_raw_events(event_buffer, partition_name="stream")
                            print(f"[Jetstream Streamer] Flushed {len(event_buffer)} events (Total: {total_processed}, result: {res})")
                            event_buffer = []

                        if max_events and total_processed >= max_events:
                            print(f"[Jetstream Streamer] Reached max_events limit ({max_events}). Stopping.")
                            self.running = False
                            break

            except (websockets.ConnectionClosed, OSError, Exception) as exc:
                print(f"[Jetstream Streamer] Connection interrupted: {exc}. Reconnecting in 5s...")
                await asyncio.sleep(5)

        # Final buffer flush
        if event_buffer:
            res = self.storage.save_raw_events(event_buffer, partition_name="stream")
            print(f"[Jetstream Streamer] Final flush: saved {len(event_buffer)} events (Result: {res})")
