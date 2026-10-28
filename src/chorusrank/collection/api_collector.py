"""Targeted historical and campaign collector using the Bluesky AT Protocol REST API."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..storage.schema import RawEventRecord
from ..storage.db import ChorusStorage


class BlueskyAPICollector:
    """Collects historical posts, replies, and repost networks via AT Protocol XRPC."""

    def __init__(
        self,
        base_url: str = "https://api.bsky.app/xrpc",
        storage: Optional[ChorusStorage] = None,
        delay_sec: float = 0.15,
    ):
        self.base_url = base_url.rstrip("/")
        self.storage = storage or ChorusStorage()
        self.delay_sec = delay_sec
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ChorusRank-Research/0.1.0"})

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Perform a robust GET request with retry backoff."""
        url = f"{self.base_url}/{endpoint}"
        resp = self.session.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            time.sleep(retry_after)
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    def search_posts(
        self,
        query: str,
        total_limit: int = 1000,
        page_size: int = 100,
        since: Optional[str] = None,
        until: Optional[str] = None,
        save_batch_size: int = 500,
    ) -> List[RawEventRecord]:
        """Search for posts matching a keyword/campaign query, paginating with cursors."""
        collected: List[RawEventRecord] = []
        cursor: Optional[str] = None
        count = 0

        print(f"[API Collector] Searching posts for query: '{query}' (limit={total_limit})...")

        while count < total_limit:
            params: Dict[str, Any] = {
                "q": query,
                "limit": min(page_size, total_limit - count),
            }
            if cursor:
                params["cursor"] = cursor
            if since:
                params["since"] = since
            if until:
                params["until"] = until

            try:
                data = self._get("app.bsky.feed.searchPosts", params=params)
            except Exception as e:
                print(f"[API Collector] Error querying '{query}': {e}")
                break

            posts = data.get("posts", [])
            if not posts:
                print(f"[API Collector] No more posts found for '{query}'.")
                break

            now_iso = datetime.now(timezone.utc).isoformat()
            batch_records: List[RawEventRecord] = []

            for p in posts:
                author_did = p.get("author", {}).get("did", "")
                uri = p.get("uri", "")
                rkey = uri.split("/")[-1] if "/" in uri else ""
                record_content = p.get("record", {})
                
                # Check for reply parents
                reply = record_content.get("reply", {})
                parent_uri = reply.get("parent", {}).get("uri")
                root_uri = reply.get("root", {}).get("uri")

                rec = RawEventRecord(
                    event_id=f"{author_did}_{rkey}",
                    did=author_did,
                    rkey=rkey,
                    cid=p.get("cid"),
                    created_at=record_content.get("createdAt", now_iso),
                    collected_at=now_iso,
                    collection="app.bsky.feed.post",
                    text=record_content.get("text", ""),
                    langs=record_content.get("langs", []),
                    reply_parent_uri=parent_uri,
                    reply_root_uri=root_uri,
                    reposted_uri=None,
                    facets=record_content.get("facets", []),
                    stratum="targeted",
                    query_tag=query,
                    post_uri=uri,
                    like_count=p.get("likeCount", 0),
                    repost_count=p.get("repostCount", 0),
                    reply_count=p.get("replyCount", 0),
                    quote_count=p.get("quoteCount", 0),
                    is_deleted=False,
                    last_checked_at=now_iso,
                )
                batch_records.append(rec)

            collected.extend(batch_records)
            count += len(batch_records)

            if len(collected) >= save_batch_size:
                saved_path = self.storage.save_raw_events(collected, partition_name="search")
                print(f"[API Collector] Flushed {len(collected)} posts to {saved_path.name}")
                collected = []

            cursor = data.get("cursor")
            if not cursor:
                break

            time.sleep(self.delay_sec)

        # Flush any remaining
        if collected:
            saved_path = self.storage.save_raw_events(collected, partition_name="search")
            print(f"[API Collector] Final flush: saved {len(collected)} posts to {saved_path.name}")

        print(f"[API Collector] Finished query '{query}'. Total captured: {count}")
        return collected

    def get_reposters_for_uri(self, post_uri: str, limit: int = 100) -> List[RawEventRecord]:
        """Fetch accounts that amplified a specific post to build co-activity repost edges."""
        records: List[RawEventRecord] = []
        try:
            data = self._get("app.bsky.feed.getRepostedBy", params={"uri": post_uri, "limit": limit})
        except Exception as e:
            print(f"[API Collector] Error fetching reposters for {post_uri}: {e}")
            return []

        reposted_by = data.get("repostedBy", [])
        now_iso = datetime.now(timezone.utc).isoformat()

        for user in reposted_by:
            user_did = user.get("did")
            if user_did:
                records.append(
                    RawEventRecord(
                        event_id=f"repost_{user_did}_{post_uri.split('/')[-1]}",
                        did=user_did,
                        rkey="",
                        created_at=now_iso,
                        collected_at=now_iso,
                        collection="app.bsky.feed.repost",
                        reposted_uri=post_uri,
                        text=None,
                    )
                )

        if records:
            self.storage.save_raw_events(records, partition_name="reposts")
        return records
