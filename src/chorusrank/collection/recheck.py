"""Longitudinal account status tracker to capture deactivations, takedowns, and moderation tags."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import requests

from ..storage.schema import AccountStatusRecord
from ..storage import get_storage


def classify_status_and_labels(
    status_code: Optional[int],
    data: Optional[Dict[str, Any]],
    raw_error: Optional[Dict[str, Any]],
    err_name: str,
    err_msg: str,
) -> Dict[str, Any]:
    """Classify account lifecycle status and generate multi-column evaluation labels."""
    raw_labels: List[dict] = []
    label_values: List[str] = []

    # 1. Active Account (HTTP 200)
    if status_code == 200 and data:
        raw_labels = data.get("labels", []) or []
        label_values = [
            lbl.get("val") for lbl in raw_labels if isinstance(lbl, dict) and lbl.get("val")
        ]
        lower_vals = [v.lower() for v in label_values]
        joined = " ".join(lower_vals)

        has_suspend = "!suspend" in joined or "suspend" in joined
        has_takedown = "!takedown" in joined or "takedown" in joined
        has_spam = "spam" in joined or "scam" in joined

        if has_suspend or has_takedown:
            status = "SUSPENDED_LABEL"
            cat = "ACTIVE_LABELED"
            strict_takedown = True
            broad_inauth = True
        elif has_spam:
            status = "SPAM_LABEL"
            cat = "ACTIVE_LABELED"
            strict_takedown = False
            broad_inauth = True
        elif len(label_values) > 0:
            status = "ACTIVE"
            cat = "ACTIVE_LABELED"
            strict_takedown = False
            broad_inauth = False
        else:
            status = "ACTIVE"
            cat = "ACTIVE_CLEAN"
            strict_takedown = False
            broad_inauth = False

        return {
            "status": status,
            "raw_labels": raw_labels,
            "label_values": label_values,
            "label_strict_takedown": strict_takedown,
            "label_broad_inauthentic": broad_inauth,
            "label_churn": False,
            "label_category": cat,
        }

    # 2. Inactive / Error Cases
    lower_name = err_name.lower()
    lower_msg = err_msg.lower()

    if "takedown" in lower_name or "takedown" in lower_msg or "suspended" in lower_msg:
        status = "TAKEDOWN"
        cat = "TAKEDOWN"
        strict_takedown = True
        broad_inauth = True
        churn = False
    elif "deactivated" in lower_name or "deactivated" in lower_msg:
        status = "DEACTIVATED"
        cat = "DEACTIVATED"
        strict_takedown = False
        broad_inauth = True
        churn = True
    elif "not found" in lower_msg or "notfound" in lower_name:
        status = "NOT_FOUND"
        cat = "PURGED_OR_MISSING"
        strict_takedown = False
        broad_inauth = False
        churn = False
    elif status_code == 429:
        status = "RATE_LIMITED"
        cat = "UNKNOWN"
        strict_takedown = False
        broad_inauth = False
        churn = False
    else:
        status = "UNRECOGNIZED_ERROR"
        cat = "UNKNOWN"
        strict_takedown = False
        broad_inauth = False
        churn = False

    return {
        "status": status,
        "raw_labels": [],
        "label_values": [],
        "label_strict_takedown": strict_takedown,
        "label_broad_inauthentic": broad_inauth,
        "label_churn": churn,
        "label_category": cat,
    }


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
        """Query getProfile for a single DID and record its status and labels."""
        now_iso = datetime.now(timezone.utc).isoformat()
        url = f"{self.base_url}/app.bsky.actor.getProfile"

        try:
            resp = self.session.get(url, params={"actor": did}, timeout=10)
            status_code = resp.status_code

            if status_code == 200:
                data = resp.json()
                clf = classify_status_and_labels(
                    status_code=200,
                    data=data,
                    raw_error=None,
                    err_name="",
                    err_msg="",
                )
                return AccountStatusRecord(
                    did=did,
                    handle=data.get("handle"),
                    status=clf["status"],
                    status_code=200,
                    raw_labels=clf["raw_labels"],
                    label_values=clf["label_values"],
                    label_strict_takedown=clf["label_strict_takedown"],
                    label_broad_inauthentic=clf["label_broad_inauthentic"],
                    label_churn=clf["label_churn"],
                    label_category=clf["label_category"],
                    checked_at=now_iso,
                )

            # Capture complete error payload
            raw_payload = {}
            try:
                raw_payload = resp.json()
                err_name = raw_payload.get("error", "")
                err_msg = raw_payload.get("message", "")
            except Exception:
                err_name = resp.text
                err_msg = ""

            clf = classify_status_and_labels(
                status_code=status_code,
                data=None,
                raw_error=raw_payload,
                err_name=err_name,
                err_msg=err_msg,
            )

            return AccountStatusRecord(
                did=did,
                status=clf["status"],
                status_code=status_code,
                error_name=err_name,
                error_message=err_msg,
                raw_error=raw_payload if raw_payload else {"raw_text": resp.text},
                raw_labels=clf["raw_labels"],
                label_values=clf["label_values"],
                label_strict_takedown=clf["label_strict_takedown"],
                label_broad_inauthentic=clf["label_broad_inauthentic"],
                label_churn=clf["label_churn"],
                label_category=clf["label_category"],
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
                label_strict_takedown=False,
                label_broad_inauthentic=False,
                label_churn=False,
                label_category="UNKNOWN",
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
        status_counts: Dict[str, int] = {}

        for i, did in enumerate(dids):
            rec = self.check_did_status(did)
            records.append(rec)
            status_counts[rec.status] = status_counts.get(rec.status, 0) + 1

            if rec.label_strict_takedown or rec.label_churn:
                takedowns_found += 1
                print(f"[Longitudinal Rechecker] Status flag: {did} -> {rec.status} (Category: {rec.label_category})")

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

        url = f"{self.base_url}/app.bsky.feed.getPosts"
        now_iso = datetime.now(timezone.utc).isoformat()
        total_updated = 0
        total_deleted = 0

        for i in range(0, total_posts, batch_size):
            chunk = post_uris[i : i + batch_size]
            params = [("uris", uri) for uri in chunk]

            try:
                resp = self.session.get(url, params=params, timeout=15)
                if resp.status_code == 429:
                    time.sleep(10)
                    resp = self.session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[Post Metrics] Error querying batch {i}: {e}")
                time.sleep(1)
                continue

            found_posts = {p.get("uri"): p for p in data.get("posts", [])}
            updates = []

            for requested_uri in chunk:
                if requested_uri in found_posts:
                    post_data = found_posts[requested_uri]
                    updates.append({
                        "post_uri": requested_uri,
                        "like_count": post_data.get("likeCount", 0),
                        "repost_count": post_data.get("repostCount", 0),
                        "reply_count": post_data.get("replyCount", 0),
                        "quote_count": post_data.get("quoteCount", 0),
                        "is_deleted": False,
                        "last_checked_at": now_iso,
                    })
                    total_updated += 1
                else:
                    # Post was deleted or taken down
                    updates.append({
                        "post_uri": requested_uri,
                        "is_deleted": True,
                        "last_checked_at": now_iso,
                    })
                    total_deleted += 1

            self.storage.update_post_metrics(updates)
            time.sleep(self.delay_sec)

        print(f"[Post Metrics] Complete: {total_updated} refreshed, {total_deleted} marked deleted.")
        return {"updated": total_updated, "deleted": total_deleted}
