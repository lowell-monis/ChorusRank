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
        base_url: str = "https://public.api.bsky.app/xrpc",
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
