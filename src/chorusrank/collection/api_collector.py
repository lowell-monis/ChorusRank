"""Targeted historical and campaign collector using the Bluesky AT Protocol REST API."""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple, Set

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..storage.schema import RawEventRecord
from ..storage import get_storage


def clean_post_text(text: Optional[str]) -> str:
    """Normalize text for MinHash/LSH and NLP: lowercase, strip URLs, user mentions, and extra spaces."""
    if not text:
        return ""
    # Remove URLs
    cleaned = re.sub(r"https?://\S+|www\.\S+", "", text)
    # Remove handle mentions (@user.bsky.social)
    cleaned = re.sub(r"@\w+[\w\.-]*", "", cleaned)
    # Normalize whitespace and lowercase
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def extract_entities_and_edges(
    record_content: Dict[str, Any], embed: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Extract URLs, domains, tags, mentions, and quote edges from AT Protocol structures."""
    urls: List[str] = []
    domains: List[str] = []
    tags: List[str] = []
    mentions: List[str] = []
    quote_uri: Optional[str] = None
    quote_did: Optional[str] = None

    # 1. Parse Facets
    facets = record_content.get("facets", []) or []
    for facet in facets:
        for feat in facet.get("features", []):
            # Links
            u = feat.get("uri")
            if u:
                urls.append(u)
                try:
                    netloc = urlparse(u).netloc.lower()
                    if netloc:
                        domains.append(netloc)
                except Exception:
                    pass
            # Mentions
            mentioned_did = feat.get("did")
            if mentioned_did:
                mentions.append(mentioned_did)
            # Tags / Hashtags
            tag = feat.get("tag")
            if tag:
                tags.append(tag.lower())

    # 2. Parse Embed
    if embed:
        # External URL cards (e.g. news links, telegram preview cards)
        ext_uri = embed.get("external", {}).get("uri")
        if ext_uri:
            urls.append(ext_uri)
            try:
                netloc = urlparse(ext_uri).netloc.lower()
                if netloc and netloc not in domains:
                    domains.append(netloc)
            except Exception:
                pass

        # Quote post edges
        embed_type = embed.get("$type", "")
        if "app.bsky.embed.record" in embed_type:
            rec_obj = embed.get("record", {})
            # Handle recordWithMedia nesting
            if "record" in rec_obj and isinstance(rec_obj["record"], dict):
                quote_uri = rec_obj["record"].get("uri")
            else:
                quote_uri = rec_obj.get("uri")

            if quote_uri and quote_uri.startswith("at://"):
                parts = quote_uri.split("/")
                if len(parts) >= 3:
                    quote_did = parts[2]

    # Deduplicate while preserving order
    return {
        "urls": list(dict.fromkeys(urls)),
        "domains": list(dict.fromkeys(domains)),
        "tags": list(dict.fromkeys(tags)),
        "mentions": list(dict.fromkeys(mentions)),
        "quote_uri": quote_uri,
        "quote_did": quote_did,
    }


class BlueskyAPICollector:
    """Collects historical posts, replies, and repost networks via AT Protocol XRPC."""

    def __init__(
        self,
        base_url: str = "https://api.bsky.app/xrpc",
        storage=None,
        delay_sec: float = 0.15,
    ):
        self.base_url = base_url.rstrip("/")
        self.storage = storage or get_storage()
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
        """Search for posts matching a keyword/campaign query, paginating with timestamps."""
        collected: List[RawEventRecord] = []
        current_until: Optional[str] = until
        last_until: Optional[str] = None
        seen_event_ids: Set[str] = set()
        count = 0

        print(f"[API Collector] Searching posts for query: '{query}' (limit={total_limit})...")

        while count < total_limit:
            params: Dict[str, Any] = {
                "q": query,
                "limit": min(page_size, total_limit - count),
            }
            if since:
                params["since"] = since
            if current_until:
                params["until"] = current_until

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
                author_obj = p.get("author", {}) or {}
                author_did = author_obj.get("did", "")
                author_handle = author_obj.get("handle")
                author_labels = author_obj.get("labels", []) or []
                author_label_vals = [lbl.get("val") for lbl in author_labels if isinstance(lbl, dict) and lbl.get("val")]

                uri = p.get("uri", "")
                rkey = uri.split("/")[-1] if "/" in uri else ""
                event_id = f"{author_did}_{rkey}"
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)

                record_content = p.get("record", {}) or {}
                post_text = record_content.get("text", "")
                
                # Check for reply edges
                reply = record_content.get("reply", {}) or {}
                parent_uri = reply.get("parent", {}).get("uri")
                root_uri = reply.get("root", {}).get("uri")
                parent_did = parent_uri.split("/")[2] if parent_uri and parent_uri.startswith("at://") else None
                root_did = root_uri.split("/")[2] if root_uri and root_uri.startswith("at://") else None

                # Extract entities, domains, tags, mentions, and quotes
                embed = p.get("embed")
                extracted = extract_entities_and_edges(record_content, embed)

                # Post-level moderation labels
                post_labels = p.get("labels", []) or []
                post_label_vals = [lbl.get("val") for lbl in post_labels if isinstance(lbl, dict) and lbl.get("val")]

                rec = RawEventRecord(
                    event_id=event_id,
                    did=author_did,
                    handle=author_handle,
                    rkey=rkey,
                    cid=p.get("cid"),
                    created_at=record_content.get("createdAt", now_iso),
                    collected_at=now_iso,
                    collection="app.bsky.feed.post",
                    text=post_text,
                    clean_text=clean_post_text(post_text),
                    langs=record_content.get("langs", []),
                    urls=extracted["urls"],
                    domains=extracted["domains"],
                    tags=extracted["tags"],
                    mentions=extracted["mentions"],
                    char_length=len(post_text),
                    word_count=len(post_text.split()),
                    url_count=len(extracted["urls"]),
                    domain_count=len(extracted["domains"]),
                    tag_count=len(extracted["tags"]),
                    mention_count=len(extracted["mentions"]),
                    reply_parent_uri=parent_uri,
                    reply_parent_did=parent_did,
                    reply_root_uri=root_uri,
                    reply_root_did=root_did,
                    quote_uri=extracted["quote_uri"],
                    quote_did=extracted["quote_did"],
                    reposted_uri=None,
                    reposted_did=None,
                    facets=record_content.get("facets", []),
                    embed=embed,
                    stratum="targeted",
                    query_tag=query,
                    post_uri=uri,
                    like_count=p.get("likeCount", 0),
                    repost_count=p.get("repostCount", 0),
                    reply_count=p.get("replyCount", 0),
                    quote_count=p.get("quoteCount", 0),
                    is_deleted=False,
                    last_checked_at=now_iso,
                    post_labels=post_labels,
                    post_label_values=post_label_vals,
                    author_labels=author_labels,
                    author_label_values=author_label_vals,
                    raw_payload=p,
                )
                batch_records.append(rec)

            collected.extend(batch_records)
            count += len(batch_records)

            if len(collected) >= save_batch_size:
                res = self.storage.save_raw_events(collected, partition_name="search")
                print(f"[API Collector] Flushed {len(collected)} posts (Storage result: {res})")
                collected = []

            # Update pagination cursor via oldest post createdAt
            oldest_created_at = posts[-1].get("record", {}).get("createdAt")
            if not oldest_created_at or oldest_created_at == last_until:
                break
            last_until = oldest_created_at
            current_until = oldest_created_at

            time.sleep(self.delay_sec)

        # Flush any remaining
        if collected:
            res = self.storage.save_raw_events(collected, partition_name="search")
            print(f"[API Collector] Final flush: saved {len(collected)} posts (Storage result: {res})")

        print(f"[API Collector] Finished query '{query}'. Total captured: {count}")
        return collected
