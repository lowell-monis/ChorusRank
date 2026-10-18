"""Import existing Bluesky JSON data (e.g. from mongo_export_bluesky.json) into ChorusRank Parquet format."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from chorusrank.storage.schema import RawEventRecord
from chorusrank.storage.db import ChorusStorage


def import_legacy_json(file_path: str, storage: ChorusStorage, batch_size: int = 5000) -> int:
    path = Path(file_path)
    if not path.exists():
        print(f"Error: file not found at {path}")
        return 0

    print(f"Importing legacy posts from {path}...")
    records: List[RawEventRecord] = []
    total_imported = 0

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            did = item.get("did", "")
            commit = item.get("commit", {})
            rkey = commit.get("rkey", "")
            collection = commit.get("collection", "app.bsky.feed.post")
            record = commit.get("record", {})
            created_at = record.get("createdAt") or item.get("collected_at", "")
            collected_at = item.get("collected_at", created_at)

            # Reply parents
            reply = record.get("reply", {})
            parent_uri = reply.get("parent", {}).get("uri")
            root_uri = reply.get("root", {}).get("uri")

            rec = RawEventRecord(
                event_id=f"{did}_{rkey}",
                did=did,
                rkey=rkey,
                cid=item.get("cid"),
                created_at=created_at,
                collected_at=collected_at,
                collection=collection,
                text=record.get("text"),
                langs=record.get("langs", []),
                reply_parent_uri=parent_uri,
                reply_root_uri=root_uri,
                facets=record.get("facets", []),
                query_tag="legacy_import",
            )
            records.append(rec)
            total_imported += 1

            if len(records) >= batch_size:
                saved = storage.save_raw_events(records, partition_name="legacy")
                print(f"Imported batch of {len(records)} events -> {saved.name}")
                records = []

    if records:
        saved = storage.save_raw_events(records, partition_name="legacy")
        print(f"Final batch of {len(records)} events -> {saved.name}")

    print(f"Import complete! Total events imported: {total_imported}")
    return total_imported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import legacy Bluesky JSON into ChorusRank")
    parser.add_argument(
        "--file",
        type=str,
        default=r"C:\Users\lowel\OneDrive - Michigan State University\Courses\SS26 CSE482 Liu, Hui\project\mongo_export_bluesky.json",
        help="Path to mongo_export_bluesky.json",
    )
    args = parser.parse_args()

    storage = ChorusStorage()
    import_legacy_json(args.file, storage)
