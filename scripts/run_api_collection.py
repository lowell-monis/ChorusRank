"""CLI script to run targeted AT Protocol search collection."""

from __future__ import annotations

import argparse
from pathlib import Path
import yaml

from chorusrank.collection.api_collector import BlueskyAPICollector
from chorusrank.storage import get_storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect historical/targeted posts via Bluesky API")
    parser.add_argument("--query", type=str, nargs="+", help="One or more search terms")
    parser.add_argument("--limit", type=int, default=2000, help="Max posts per query")
    parser.add_argument("--config", type=str, default="config/config.yaml", help="Path to config file")
    parser.add_argument("--since", type=str, default=None, help="Earliest post creation ISO timestamp (e.g. 2026-10-01T00:00:00Z)")
    parser.add_argument("--until", type=str, default=None, help="Latest post creation ISO timestamp (e.g. 2026-10-28T00:00:00Z)")
    args = parser.parse_args()

    queries = args.query
    limit = args.limit

    if not queries and Path(args.config).exists():
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            api_cfg = cfg.get("collection", {}).get("api", {})
            queries = api_cfg.get("target_queries", ["airdrop", "crypto"])
            limit = api_cfg.get("posts_per_query", 2000)

    if not queries:
        queries = ["airdrop", "claim bonus", "join telegram"]

    storage = get_storage()
    collector = BlueskyAPICollector(storage=storage)

    for q in queries:
        collector.search_posts(
            query=q,
            total_limit=limit,
            since=args.since,
            until=args.until,
        )

    summary = storage.get_dataset_summary()
    print("\n=== Dataset Summary After API Collection ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
