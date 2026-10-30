"""CLI script to run live Bluesky Jetstream firehose collection with real-time keyword filtering."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import yaml

from chorusrank.collection.stream_collector import JetstreamStreamCollector
from chorusrank.collection.api_collector import TARGET_KEYWORDS_POOL
from chorusrank.storage import get_storage


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Stream live events from Bluesky Jetstream with real-time keyword filtering")
    parser.add_argument("--filter-keywords", action="store_true", help="Filter live stream for campaign keywords (default: enabled)")
    parser.add_argument("--keywords", type=str, nargs="+", default=None, help="Custom list of keywords to filter on")
    parser.add_argument("--modulo", type=int, default=1, help="DID hash sample modulo (1 = 100% of matches, 10 = 10% sampling for prototypes)")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N matching events (None to run continuously)")
    parser.add_argument("--config", type=str, default="config/config.yaml", help="Path to config file")
    args = parser.parse_args()

    sample_modulo = args.modulo
    kw_filter = None

    if args.keywords:
        kw_filter = [k.lower() for k in args.keywords]
    elif args.filter_keywords or True:  # Default to target keywords pool for campaign tracking
        kw_filter = TARGET_KEYWORDS_POOL

    storage = get_storage()
    streamer = JetstreamStreamCollector(
        sample_modulo=sample_modulo,
        filter_keywords=kw_filter,
        require_english=True,
        require_text_heavy=True,
        storage=storage,
    )

    try:
        await streamer.stream_indefinitely(max_events=args.limit)
    except KeyboardInterrupt:
        print("\n[Jetstream Streamer] Interrupted by user. Exiting cleanly.")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
