"""CLI script to run live Bluesky Jetstream firehose collection."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import yaml

from chorusrank.collection.stream_collector import JetstreamStreamCollector
from chorusrank.storage.db import ChorusStorage


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Stream live events from Bluesky Jetstream")
    parser.add_argument("--modulo", type=int, default=1, help="DID hash sample modulo (1 = 100%, 10 = 10%)")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N events (None for indefinite)")
    parser.add_argument("--config", type=str, default="config/config.yaml", help="Path to config file")
    args = parser.parse_args()

    sample_modulo = args.modulo
    if Path(args.config).exists():
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            sample_modulo = cfg.get("collection", {}).get("jetstream", {}).get("sample_modulo", sample_modulo)

    storage = ChorusStorage()
    streamer = JetstreamStreamCollector(sample_modulo=sample_modulo, storage=storage)

    try:
        await streamer.stream_indefinitely(max_events=args.limit)
    except KeyboardInterrupt:
        print("\n[Jetstream Streamer] Interrupted by user. Exiting cleanly.")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
