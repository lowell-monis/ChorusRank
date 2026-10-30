"""CLI script to run longitudinal takedowns, deactivations, and post engagement/deletion checks."""

from __future__ import annotations

import argparse
import os
import sys

from chorusrank.collection.recheck import LongitudinalRechecker
from chorusrank.storage import get_storage
from chorusrank.storage.mongo import MongoStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe accounts and refresh post metrics/deletions")
    parser.add_argument("--account-limit", type=int, default=None, help="Max accounts to probe")
    parser.add_argument("--post-limit", type=int, default=None, help="Max posts to refresh")
    args = parser.parse_args()

    storage = get_storage()
    if os.getenv("STORAGE_BACKEND") == "mongodb" and not isinstance(storage, MongoStorage):
        print("\n[CRITICAL ERROR] Failed to connect to MongoDB Atlas in automated recheck job!")
        print("  1. Verify the 'MONGO_URI' secret is set in GitHub Repository Secrets.")
        print("  2. Verify that MongoDB Atlas allows access from GitHub Actions (Network Access -> 0.0.0.0/0).")
        sys.exit(1)

    rechecker = LongitudinalRechecker(storage=storage)

    print("\n--- [1/2] Checking Account Longitudinal Status ---")
    rechecker.run_daily_recheck(sample_limit=args.account_limit)

    print("\n--- [2/2] Refreshing Post Engagement & Deletions ---")
    rechecker.refresh_post_metrics(sample_limit=args.post_limit)


if __name__ == "__main__":
    main()
