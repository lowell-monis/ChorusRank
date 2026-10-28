"""Longitudinal account status tracker to capture deactivations and takedowns."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from ..storage.schema import AccountStatusRecord
from ..storage import get_storage


class LongitudinalRechecker:
    """Probes observed DIDs daily to detect account suspensions, takedowns, and deactivations."""

    def __init__(
        self,
        base_url: str = "https://api.bsky.app/xrpc",
        storage=None,
        delay_sec: float = 0.1,
    ):
        self.base_url = base_url.rstrip("/")
        self.storage = storage or get_storage()
        self.delay_sec = delay_sec
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ChorusRank-Research/0.1.0"})

    def check_did_status(self, did: str) -> AccountStatusRecord:
        """Query getProfile for a single DID and record its current status without losing error data."""
        now_iso = datetime.now(timezone.utc).isoformat()
        url = f"{self.base_url}/app.bsky.actor.getProfile"

        try:
            resp = self.session.get(url, params={"actor": did}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return AccountStatusRecord(
                    did=did,
                    handle=data.get("handle"),
                    status="ACTIVE",
                    status_code=200,
                    checked_at=now_iso,
                )

            # Capture complete error payload
            status_code = resp.status_code
            raw_payload = {}
            try:
                raw_payload = resp.json()
                err_name = raw_payload.get("error", "")
                err_msg = raw_payload.get("message", "")
            except Exception:
                err_name = resp.text
                err_msg = ""

            lower_name = err_name.lower()
            lower_msg = err_msg.lower()

            # Robust status classification
            if "takedown" in lower_name or "takedown" in lower_msg or "suspended" in lower_msg:
                status = "TAKEDOWN"
            elif "deactivated" in lower_name or "deactivated" in lower_msg:
                status = "DEACTIVATED"
            elif "not found" in lower_msg or "notfound" in lower_name:
                status = "NOT_FOUND"
            elif status_code == 429:
                status = "RATE_LIMITED"
            else:
                status = "UNRECOGNIZED_ERROR"

            return AccountStatusRecord(
                did=did,
                status=status,
                status_code=status_code,
                error_name=err_name,
                error_message=err_msg,
                raw_error=raw_payload if raw_payload else {"raw_text": resp.text},
                checked_at=now_iso,
            )

        except requests.RequestException as e:
            return AccountStatusRecord(
                did=did,
                status="NETWORK_ERROR",
                status_code=None,
                error_name="RequestException",
                error_message=str(e),
                raw_error={"exception": str(e)},
                checked_at=now_iso,
            )

    def run_daily_recheck(self, sample_limit: Optional[int] = None) -> List[AccountStatusRecord]:
        """Run status checks across all observed accounts."""
        dids = self.storage.get_distinct_dids()
        if sample_limit:
            dids = dids[:sample_limit]

        print(f"[Longitudinal Rechecker] Starting daily probe for {len(dids)} accounts...")
        records: List[AccountStatusRecord] = []
        takedowns_found = 0
        status_counts = {}

        for i, did in enumerate(dids):
            rec = self.check_did_status(did)
            records.append(rec)
            status_counts[rec.status] = status_counts.get(rec.status, 0) + 1

            if rec.status in ("TAKEDOWN", "DEACTIVATED"):
                takedowns_found += 1
                print(f"[Longitudinal Rechecker] Status flag: {did} -> {rec.status} ({rec.error_message})")

            if len(records) >= 500:
                self.storage.save_status_checks(records)
                print(f"[Longitudinal Rechecker] Saved batch of {len(records)} status records (Progress: {i+1}/{len(dids)})")
                records = []

            time.sleep(self.delay_sec)

        if records:
            self.storage.save_status_checks(records)
            print(f"[Longitudinal Rechecker] Final save: {len(records)} records")

        print(f"[Longitudinal Rechecker] Finished probe across {len(dids)} accounts.")
        print(f"[Longitudinal Rechecker] Summary breakdown: {status_counts}")
        return records

    def refresh_post_metrics(self, sample_limit: Optional[int] = None, batch_size: int = 25) -> dict:
        """Query getPosts in batches of 25 to refresh like/repost counts and detect deleted posts."""
        if not hasattr(self.storage, "get_post_uris"):
            print("[Post Metrics] Current storage backend does not support get_post_uris.")
            return {"updated": 0, "deleted": 0}

        post_uris = self.storage.get_post_uris(sample_limit=sample_limit)
        total_posts = len(post_uris)
        print(f"[Post Metrics] Starting engagement & deletion check across {total_posts} posts...")

        if total_posts == 0:
            return {"updated": 0, "deleted": 0}

        now_iso = datetime.now(timezone.utc).isoformat()
        total_updated = 0
        total_deleted = 0
        batch_size = min(25, max(1, batch_size))
        url = f"{self.base_url}/app.bsky.feed.getPosts"

        for i in range(0, total_posts, batch_size):
            chunk = post_uris[i : i + batch_size]
            params = [("uris", uri) for uri in chunk]

            try:
                resp = self.session.get(url, params=params, timeout=15)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 10))
                    time.sleep(retry_after)
                    resp = self.session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[Post Metrics] Error fetching batch {i}: {e}")
                time.sleep(1)
                continue

            returned_posts = {p.get("uri"): p for p in data.get("posts", [])}
            updates = []

            for uri in chunk:
                if uri in returned_posts:
                    p = returned_posts[uri]
                    updates.append({
                        "post_uri": uri,
                        "like_count": p.get("likeCount", 0),
                        "repost_count": p.get("repostCount", 0),
                        "reply_count": p.get("replyCount", 0),
                        "quote_count": p.get("quoteCount", 0),
                        "is_deleted": False,
                        "last_checked_at": now_iso,
                    })
                    total_updated += 1
                else:
                    # Post was requested but omitted from response -> Deleted!
                    updates.append({
                        "post_uri": uri,
                        "is_deleted": True,
                        "last_checked_at": now_iso,
                    })
                    total_deleted += 1

            if updates:
                self.storage.update_post_metrics(updates)

            time.sleep(self.delay_sec)

        print(f"[Post Metrics] Finished. Active posts refreshed: {total_updated}, Deleted posts found: {total_deleted}")
        return {"updated": total_updated, "deleted": total_deleted}
