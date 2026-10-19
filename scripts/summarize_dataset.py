"""Generate dataset statistics, schema dimensions, and error summaries for the Intermediate Report."""

from __future__ import annotations

from chorusrank.storage import get_storage, MongoStorage, ChorusStorage


def format_bytes(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def main() -> None:
    storage = get_storage()
    summary = storage.get_dataset_summary()
    total_dids = len(storage.get_distinct_dids())
    enriched_dids = len(storage.get_already_enriched_dids())

    print("\n" + "=" * 65)
    print("           CHORUSRANK: INTERMEDIATE DATASET PROFILE")
    print("=" * 65)
    if isinstance(storage, MongoStorage):
        print(f"Primary Storage Backend: MongoDB Atlas (NoSQL)")
        print(f"Database Name:           {summary.get('database_name', 'chorusrank')}")
    else:
        print(f"Primary Storage Backend: Parquet / DuckDB (Local)")

    print("-" * 65)
    print(f"Total Raw Events:        {summary.get('total_events', 0):,}")
    print(f"  • Posts:               {int(summary.get('posts_count', 0)):,}")
    print(f"  • Reposts:             {int(summary.get('reposts_count', 0)):,}")
    print(f"Unique Observed DIDs:    {total_dids:,}")
    print(f"Profiles Enriched:       {enriched_dids:,}")
    if isinstance(storage, MongoStorage):
        print(f"Takedowns Detected:      {summary.get('takedowns_detected', 0):,}")
    print(f"Earliest Event:          {summary.get('earliest_event', 'N/A')}")
    print(f"Latest Event:            {summary.get('latest_event', 'N/A')}")

    if isinstance(storage, ChorusStorage):
        total_raw_bytes = sum(f.stat().st_size for f in storage.raw_dir.glob("*.parquet"))
        total_enriched_bytes = sum(f.stat().st_size for f in storage.enriched_dir.glob("*.parquet"))
        print("-" * 65)
        print("STORAGE FOOTPRINT (PARQUET)")
        print(f"  • Raw Events Store:    {format_bytes(total_raw_bytes)}")
        print(f"  • Enriched Profiles:   {format_bytes(total_enriched_bytes)}")
        print(f"  • Total Footprint:     {format_bytes(total_raw_bytes + total_enriched_bytes)}")

    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
