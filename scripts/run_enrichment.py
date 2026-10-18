"""CLI script to enrich raw accounts with profile metadata."""

from __future__ import annotations

import argparse
from chorusrank.collection.enricher import ProfileEnricher
from chorusrank.storage.db import ChorusStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich collected DIDs with profile metadata")
    parser.add_argument("--limit", type=int, default=None, help="Max accounts to enrich (default: all pending)")
    args = parser.parse_args()

    storage = ChorusStorage()
    enricher = ProfileEnricher(storage=storage)
    enricher.enrich_unprofiled_accounts(max_accounts=args.limit)


if __name__ == "__main__":
    main()
