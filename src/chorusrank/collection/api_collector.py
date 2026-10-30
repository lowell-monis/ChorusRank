"""Targeted historical and campaign collector using the Bluesky AT Protocol REST API.

Features:
- English language filtering (metadata tag + lexical heuristic)
- Text-heavy post enforcement (minimum characters/words, excludes pure hashtags or image-only posts)
- Keyword presence verification & combinatoric query support
- Entity & graph edge extraction (URLs, domains, mentions, tags, quotes, replies)
- Repost amplifier tracking via getRepostedBy (replicating & generalizing coordination detection literature)
- Native timestamp pagination to avoid Cloudflare 403 WAF blocks
"""

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


# Common English function words used for lexical language detection when 'langs' is missing
ENGLISH_FUNCTION_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for", "not",
    "on", "with", "he", "as", "you", "do", "at", "this", "but", "his", "by", "from",
    "they", "we", "say", "her", "she", "or", "an", "will", "my", "one", "all", "would",
    "there", "their", "what", "so", "up", "out", "if", "about", "who", "get", "which",
    "go", "me", "when", "make", "can", "like", "time", "no", "just", "him", "know",
    "take", "people", "into", "year", "your", "good", "some", "could", "them", "see",
    "other", "than", "then", "now", "look", "only", "come", "its", "over", "think",
    "also", "back", "after", "use", "two", "how", "our", "work", "first", "well",
    "way", "even", "new", "want", "because", "any", "these", "give", "day", "most", "us",
    "are", "is", "was", "were", "been", "has", "had", "should", "must"
}

# Core target campaign keywords pool
TARGET_KEYWORDS_POOL = [
    "recover", "coinbase", "binance", "wallet", "recovery", "airdrop", "giveaway",
    "free", "presale", "telegram", "yoroi", "whitelist", "white paper", "whitepaper",
    "$eth", "$sol", "$xrp", "mr beast", "mrbeast", "btc", "eth", "xrp", "sol",
    "dyor", "safu", "wagmi", "ngmi", "drop", "address", "addresss", "drop address",
    "presale event", "event", "metamask", "x gem", "gem", "urgent", "update",
    "urgent update", "limited time only", "limited", "limited time", "t.me", "crypto"
]


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


def is_english_post(text: str, langs: Optional[List[str]] = None) -> bool:
    """Check if post is English using metadata tag or lexical heuristic."""
    # 1. Use explicit metadata tag if provided by AT Protocol client
    if langs:
        if any(l.lower().startswith("en") for l in langs):
            return True
        # Explicit non-English tag (e.g. ja, ru, ar, es) -> exclude
        return False

    # 2. Heuristic check if metadata tag was omitted
    if not text:
        return False
    # Check ASCII character ratio (excludes non-Latin scripts like Cyrillic, Arabic, Chinese)
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(1, len(text))
    if ascii_ratio < 0.75:
        return False

    # Check for presence of common English function words
    tokens = set(re.findall(r"\b[a-zA-Z]{2,}\b", text.lower()))
    common_matches = tokens.intersection(ENGLISH_FUNCTION_WORDS)
    return len(common_matches) >= 2


def is_text_heavy_and_valid(
    text: str,
    embed: Optional[Dict[str, Any]] = None,
    min_words: int = 5,
    min_chars: int = 25,
) -> bool:
    """Ensure post is text-heavy and reject pure hashtag or image-dominated posts.
    
    Rules:
    1. Minimum character length and total word count.
    2. Stripping hashtags must leave at least 4 meaningful words (no only-hashtag posts).
    3. If post embeds images, require at least 8 non-hashtag words to prevent image+tag spam.
    """
    if not text:
        return False

    words = text.split()
    if len(words) < min_words or len(text) < min_chars:
        return False

    # Rule 2: Strip hashtags and check remaining substantive words
    text_without_hashtags = re.sub(r"#\S+", "", text).strip()
    words_without_hashtags = text_without_hashtags.split()
    if len(words_without_hashtags) < 4:
        return False

    # Rule 3: Avoid posts that are primarily images with minimal caption
    if embed:
        embed_type = embed.get("$type", "")
        if "app.bsky.embed.images" in embed_type or "app.bsky.embed.recordWithMedia" in embed_type:
            if len(words_without_hashtags) < 8:
                return False

    return True


def contains_target_keywords(text: str, keywords: Optional[List[str]] = None) -> bool:
    """Check if post text contains one or more target campaign keywords."""
    if not text:
        return False
    lower_text = text.lower()
    pool = keywords or TARGET_KEYWORDS_POOL
    for kw in pool:
        if kw in lower_text:
            return True
    return False


def extract_entities_and_edges(
    record_content: Dict[str, Any], embed: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Extract all URLs, domains, tags, mentions, and quote edges from AT Protocol structures."""
    urls: List[str] = []
    domains: List[str] = []
    tags: List[str] = []
    mentions: List[str] = []
    quote_uri: Optional[str] = None
    quote_did: Optional[str] = None

    # 1. Parse Facets (rich text links, mentions, tags)
    facets = record_content.get("facets", []) or []
    for facet in facets:
        for feat in facet.get("features", []):
            # Links (captures ANY domain: t.me, bit.ly, discord.gg, exchange sites, etc.)
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

    # 2. Parse Embeds (external preview cards and quotes)
    if embed:
        # External URL cards
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
        require_english: bool = True,
        require_text_heavy: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.storage = storage or get_storage()
        self.delay_sec = delay_sec
        self.require_english = require_english
        self.require_text_heavy = require_text_heavy
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
        """Search for posts matching a query with content quality and language filters."""
        collected: List[RawEventRecord] = []
        current_until: Optional[str] = until
        last_until: Optional[str] = None
        seen_event_ids: Set[str] = set()
        count = 0
        filtered_out = 0

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

                uri = p.get("uri", "")
                rkey = uri.split("/")[-1] if "/" in uri else ""
                if not author_did or not rkey or not uri:
                    continue

                event_id = f"{author_did}_{rkey}"
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)

                record_content = p.get("record", {}) or {}
                post_text = record_content.get("text", "")
                embed = p.get("embed")
                langs = record_content.get("langs", [])

                # 1. Filter: Restrict to English
                if self.require_english and not is_english_post(post_text, langs):
                    filtered_out += 1
                    continue

                # 2. Filter: Restrict to text-heavy and reject hashtag-only / image-only posts
                if self.require_text_heavy and not is_text_heavy_and_valid(post_text, embed):
                    filtered_out += 1
                    continue

                # 3. Filter: Keyword verification
                if not contains_target_keywords(post_text):
                    filtered_out += 1
                    continue

                # Parse reply edges
                reply = record_content.get("reply", {}) or {}
                parent_uri = reply.get("parent", {}).get("uri")
                root_uri = reply.get("root", {}).get("uri")
                parent_did = parent_uri.split("/")[2] if parent_uri and parent_uri.startswith("at://") else None
                root_did = root_uri.split("/")[2] if root_uri and root_uri.startswith("at://") else None

                # Extract entities, domains, tags, mentions, and quotes
                extracted = extract_entities_and_edges(record_content, embed)

                # Post-level moderation labels
                post_labels = p.get("labels", []) or []
                post_label_vals = [lbl.get("val") for lbl in post_labels if isinstance(lbl, dict) and lbl.get("val")]
                author_labels = author_obj.get("labels", []) or []
                author_label_vals = [lbl.get("val") for lbl in author_labels if isinstance(lbl, dict) and lbl.get("val")]

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
                    langs=langs,
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
                print(f"[API Collector] Flushed {len(collected)} posts (Filtered out {filtered_out} non-matching/foreign/image-only posts)")
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
            print(f"[API Collector] Final flush: saved {len(collected)} posts (Total filtered: {filtered_out})")

        print(f"[API Collector] Finished query '{query}'. Total captured: {count} (Filtered: {filtered_out})")
        return collected

    def get_reposters_for_uri(self, post_uri: str, limit: int = 100) -> List[RawEventRecord]:
        """Fetch accounts that amplified a specific post to build co-activity repost edges."""
        url = f"{self.base_url}/app.bsky.feed.getRepostedBy"
        params = {"uri": post_uri, "limit": min(limit, 100)}
        try:
            data = self._get("app.bsky.feed.getRepostedBy", params=params)
        except Exception as e:
            print(f"[API Collector] Error fetching reposters for {post_uri}: {e}")
            return []

        reposters = data.get("repostedBy", [])
        now_iso = datetime.now(timezone.utc).isoformat()
        records: List[RawEventRecord] = []

        for u in reposters:
            reposter_did = u.get("did", "")
            rkey = post_uri.split("/")[-1] if "/" in post_uri else ""
            rec = RawEventRecord(
                event_id=f"repost_{reposter_did}_{rkey}",
                did=reposter_did,
                handle=u.get("handle"),
                rkey=f"repost_{rkey}",
                created_at=u.get("createdAt") or u.get("indexedAt") or now_iso,
                collected_at=now_iso,
                collection="app.bsky.feed.repost",
                reposted_uri=post_uri,
                reposted_did=post_uri.split("/")[2] if post_uri.startswith("at://") else None,
                stratum="targeted",
                query_tag="repost_graph",
                raw_payload=u,
            )
            records.append(rec)

        return records

    def collect_repost_amplifiers(self, min_reposts: int = 1, max_posts: int = 50) -> int:
        """Scan stored posts with reposts and collect the accounts that amplified them."""
        if not hasattr(self.storage, "events_col"):
            print("[API Collector] Repost collection requires MongoDB storage.")
            return 0

        cursor = self.storage.events_col.find(
            {"collection": "app.bsky.feed.post", "repost_count": {"$gte": min_reposts}},
            {"post_uri": 1, "repost_count": 1}
        ).limit(max_posts)

        total_reposts_collected = 0
        for doc in cursor:
            uri = doc.get("post_uri")
            if not uri:
                continue
            reposter_records = self.get_reposters_for_uri(uri)
            if reposter_records:
                self.storage.save_raw_events(reposter_records, partition_name="repost")
                total_reposts_collected += len(reposter_records)
                print(f"[API Collector] Captured {len(reposter_records)} reposters for post: {uri}")
            time.sleep(self.delay_sec)

        print(f"[API Collector] Completed repost amplification collection: {total_reposts_collected} repost events stored.")
        return total_reposts_collected
