"""Batch account profile metadata enricher via AT Protocol getProfiles."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional, Set

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..storage.schema import ProfileMetadata
from ..storage.db import ChorusStorage


class ProfileEnricher:
    """Enriches raw DIDs with account-level metadata, age, and social graph metrics."""

    def __init__(
        self,
        base_url: str = "https://public.api.bsky.app/xrpc",
        storage: Optional[ChorusStorage] = None,
        batch_size: int = 25,
        delay_sec: float = 0.15,
    ):
        self.base_url = base_url.rstrip("/")
        self.storage = storage or ChorusStorage()
        self.batch_size = min(25, max(1, batch_size))  # Max allowed by XRPC is 25
        self.delay_sec = delay_sec
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ChorusRank-Research/0.1.0"})

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _fetch_batch_profiles(self, dids: List[str]) -> List[dict]:
        """Fetch profiles for up to 25 DIDs in a single request."""
        url = f"{self.base_url}/app.bsky.actor.getProfiles"
        params = [("actors", did) for did in dids]
        resp = self.session.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            time.sleep(retry_after)
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json().get("profiles", [])

    def enrich_unprofiled_accounts(self, max_accounts: Optional[int] = None) -> int:
        """Find all DIDs lacking profiles and enrich them in batches."""
        all_dids = self.storage.get_distinct_dids()
        already_enriched = self.storage.get_already_enriched_dids()

        pending_dids = [did for did in all_dids if did not in already_enriched]
        if max_accounts:
            pending_dids = pending_dids[:max_accounts]

        total_pending = len(pending_dids)
        print(f"[Profile Enricher] Found {total_pending} accounts requiring metadata enrichment.")

        if total_pending == 0:
            return 0

        now_iso = datetime.now(timezone.utc).isoformat()
        enriched_count = 0
        batch_records: List[ProfileMetadata] = []

        # Process in chunks of 25
        for i in range(0, total_pending, self.batch_size):
            chunk = pending_dids[i : i + self.batch_size]
            try:
                raw_profiles = self._fetch_batch_profiles(chunk)
            except Exception as e:
                print(f"[Profile Enricher] Error fetching batch starting at {i}: {e}")
                time.sleep(2)
                continue

            for p in raw_profiles:
                rec = ProfileMetadata(
                    did=p.get("did", ""),
                    handle=p.get("handle", ""),
                    display_name=p.get("displayName"),
                    description=p.get("description"),
                    created_at=p.get("createdAt") or p.get("indexedAt"),
                    followers_count=p.get("followersCount", 0),
                    follows_count=p.get("followsCount", 0),
                    posts_count=p.get("postsCount", 0),
                    has_avatar=bool(p.get("avatar")),
                    has_banner=bool(p.get("banner")),
                    enriched_at=now_iso,
                )
                batch_records.append(rec)

            enriched_count += len(raw_profiles)

            # Flush periodically
            if len(batch_records) >= 500:
                saved_path = self.storage.save_profiles(batch_records)
                print(f"[Profile Enricher] Saved {len(batch_records)} profiles to {saved_path.name}")
                batch_records = []

            time.sleep(self.delay_sec)

        if batch_records:
            saved_path = self.storage.save_profiles(batch_records)
            print(f"[Profile Enricher] Final save: {len(batch_records)} profiles to {saved_path.name}")

        print(f"[Profile Enricher] Enrichment complete. Successfully profiled {enriched_count} accounts.")
        return enriched_count
