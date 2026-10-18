"""Longitudinal account status tracker to capture deactivations and takedowns."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from ..storage.schema import AccountStatusRecord
from ..storage.db import ChorusStorage


class LongitudinalRechecker:
    """Probes observed DIDs daily to detect account suspensions, takedowns, and deactivations."""

    def __init__(
        self,
        base_url: str = "https://public.api.bsky.app/xrpc",
        storage: Optional[ChorusStorage] = None,
        delay_sec: float = 0.1,
    ):
        self.base_url = base_url.rstrip("/")
        self.storage = storage or ChorusStorage()
        self.delay_sec = delay_sec
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ChorusRank-Research/0.1.0"})

    def check_did_status(self, did: str) -> AccountStatusRecord:
        """Query getProfile for a single DID and record its current status."""
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

            # Error responses
            status_code = resp.status_code
            err_msg = ""
            try:
                err_json = resp.json()
                err_name = err_json.get("error", "")
                err_msg = err_json.get("message", "")
            except Exception:
                err_name = resp.text

            if "AccountTakedown" in err_name or "Takedown" in err_msg:
                status = "TAKEDOWN"
            elif "AccountNotFound" in err_name:
                status = "NOT_FOUND"
            elif "Deactivated" in err_name or "Deactivated" in err_msg:
                status = "DEACTIVATED"
            else:
                status = "SUSPICIOUS_ERROR"

            return AccountStatusRecord(
                did=did,
                status=status,
                status_code=status_code,
                error_message=f"{err_name}: {err_msg}",
                checked_at=now_iso,
            )

        except requests.RequestException as e:
            return AccountStatusRecord(
                did=did,
                status="NETWORK_ERROR",
                status_code=None,
                error_message=str(e),
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

        for i, did in enumerate(dids):
            rec = self.check_did_status(did)
            records.append(rec)

            if rec.status in ("TAKEDOWN", "DEACTIVATED"):
                takedowns_found += 1
                print(f"[Longitudinal Rechecker] Flagged status change: {did} -> {rec.status} ({rec.error_message})")

            if len(records) >= 500:
                self.storage.save_status_checks(records)
                records = []

            time.sleep(self.delay_sec)

        if records:
            self.storage.save_status_checks(records)

        print(f"[Longitudinal Rechecker] Finished probe. Identified {takedowns_found} takedowns / deactivations.")
        return records
