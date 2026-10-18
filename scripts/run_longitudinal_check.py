"""CLI script to run longitudinal takedown & deactivation status checks."""

from __future__ import annotations

import argparse
from chorusrank.collection.recheck import LongitudinalRechecker
from chorusrank.storage.db import ChorusStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe accounts for longitudinal status changes")
    parser.add_argument("--limit", type=int, default=None, help="Max accounts to check in this pass")
    args = parser.parse_args()

    storage = ChorusStorage()
    rechecker = LongitudinalRechecker(storage=storage)
    rechecker.run_daily_recheck(sample_limit=args.limit)


if __name__ == "__main__":
    main()
