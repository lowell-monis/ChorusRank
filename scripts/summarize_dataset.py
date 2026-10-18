"""Generate dataset statistics, schema dimensions, and error summaries for the Intermediate Report."""

from __future__ import annotations

from pathlib import Path
from chorusrank.storage.db import ChorusStorage


def format_bytes(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def main() -> None:
    storage = ChorusStorage()
    summary = storage.get_dataset_summary()

    # Calculate disk usage
    total_raw_bytes = sum(f.stat().st_size for f in storage.raw_dir.glob("*.parquet"))
    total_enriched_bytes = sum(f.stat().st_size for f in storage.enriched_dir.glob("*.parquet"))
    total_dids = len(storage.get_distinct_dids())
    enriched_dids = len(storage.get_already_enriched_dids())

    print("\n" + "=" * 60)
    print("        CHORUSRANK: INTERMEDIATE DATASET PROFILE")
    print("=" * 60)
    print(f"Total Raw Events:        {summary.get('total_events', 0):,}")
    print(f"  • Posts:               {summary.get('posts_count', 0):,}")
    print(f"  • Reposts:             {summary.get('reposts_count', 0):,}")
    print(f"Unique Observed DIDs:    {total_dids:,}")
    print(f"Profiles Enriched:       {enriched_dids:,}")
    print(f"Earliest Event:          {summary.get('earliest_event', 'N/A')}")
    print(f"Latest Event:            {summary.get('latest_event', 'N/A')}")
    print("-" * 60)
    print("STORAGE FOOTPRINT (PARQUET)")
    print(f"  • Raw Events Store:    {format_bytes(total_raw_bytes)}")
    print(f"  • Enriched Profiles:   {format_bytes(total_enriched_bytes)}")
    print(f"  • Total Footprint:     {format_bytes(total_raw_bytes + total_enriched_bytes)}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
