"""Audit existing stored dataset for scam signatures and deleted/banned accounts."""

from __future__ import annotations

import sys
import re
import argparse
from pathlib import Path
from typing import List, Dict, Any

import duckdb
import requests

from chorusrank.storage import get_storage
from chorusrank.storage.mongo import MongoStorage


def audit_dataset(backend_choice: str = "auto"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 70)
    print("CHORUSRANK CURRENT DATASET AUDIT (SCAMS & DELETED ACCOUNTS)")
    print("=" * 70, flush=True)

    posts: List[Dict[str, Any]] = []

    # 1. Fetch posts
    if backend_choice != "parquet":
        try:
            storage = get_storage(backend="mongodb")
            if isinstance(storage, MongoStorage):
                print(f"[Audit] Connected to MongoDB Atlas ({storage.db_name}).", flush=True)
                cursor = storage.events_col.find({"collection": "app.bsky.feed.post"})
                for doc in cursor:
                    posts.append(doc)
                print(f"[Audit] Retrieved {len(posts)} posts from MongoDB.", flush=True)
        except Exception as e:
            print(f"[Audit] MongoDB query skipped/failed ({e}), falling back to local files...", flush=True)

    if not posts:
        parquet_files = list(Path("data/raw").glob("*.parquet"))
        if parquet_files:
            print(f"[Audit] Reading from {len(parquet_files)} local Parquet files in data/raw...", flush=True)
            con = duckdb.connect()
            df = con.execute("SELECT did, handle, text, created_at, urls, domains FROM read_parquet('data/raw/*.parquet')").df()
            posts = df.to_dict(orient="records")
            print(f"[Audit] Retrieved {len(posts)} posts from local Parquet files.", flush=True)
        else:
            print("[Audit] No data found in storage.", flush=True)
            return

    total_posts = len(posts)
    unique_dids = list({p.get("did") for p in posts if p.get("did")})
    print(f"\n[Summary] Total Posts in Dataset: {total_posts} | Unique Authors: {len(unique_dids)}", flush=True)

    # 2. Check for scam content signals
    print("\n" + "-" * 70)
    print("ANALYSIS 1: SCAM & INAUTHENTIC SIGNALS IN POST TEXT")
    print("-" * 70, flush=True)

    scam_regex = re.compile(
        r"(t\.me|telegram|airdrop|giveaway|presale|recover|recovery|dm\s+to|free\s+crypto|wallet|whitelist|guaranteed|whatsapp)",
        re.IGNORECASE,
    )

    scam_matches = []
    text_counter: Dict[str, List[Any]] = {}

    for p in posts:
        txt = p.get("text") or ""
        author = p.get("handle") or p.get("did") or "unknown"
        if scam_regex.search(txt):
            scam_matches.append(p)
        clean = re.sub(r"\s+", " ", txt).strip().lower()
        if len(clean) > 20:
            text_counter.setdefault(clean, []).append((author, p.get("did"), txt))

    print(f"Posts containing targeted scam/campaign keywords: {len(scam_matches)} / {total_posts} ({len(scam_matches)/max(1, total_posts)*100:.1f}%)", flush=True)

    # Check for text repetition / spam blasts
    repetitive_texts = {k: v for k, v in text_counter.items() if len(v) > 1}
    print(f"Identical text blasts (repeated >= 2 times): {len(repetitive_texts)} distinct clusters", flush=True)

    for idx, (k, records) in enumerate(list(repetitive_texts.items())[:5]):
        print(f"\n  [Blast Cluster #{idx+1}] Repeated {len(records)} times:")
        author_handles = {r[0] for r in records}
        print(f"    Authors: {author_handles}")
        print(f"    Full Snippet: {repr(records[0][2][:120])}...")

    # 3. Check Live Account Status (Takedowns / Deletions / Suspensions)
    print("\n" + "-" * 70)
    print("ANALYSIS 2: LIVE ACCOUNT STATUS & TAKEDOWNS PROBE")
    print("-" * 70, flush=True)
    print(f"Probing Bluesky API for current status of unique authors (checking up to 30)...", flush=True)

    deleted_or_takedown = []
    moderation_labeled = []
    active_clean = []

    for i, did in enumerate(unique_dids[:30]):
        try:
            r = requests.get(
                "https://api.bsky.app/xrpc/app.bsky.actor.getProfile",
                params={"actor": did},
                timeout=4,
            )
            if r.status_code == 200:
                data = r.json()
                labels = data.get("labels", []) or []
                if labels:
                    moderation_labeled.append({"did": did, "handle": data.get("handle"), "labels": labels})
                else:
                    active_clean.append({"did": did, "handle": data.get("handle")})
            else:
                err_payload = {}
                try:
                    err_payload = r.json()
                except Exception:
                    err_payload = {"raw": r.text}
                deleted_or_takedown.append({
                    "did": did,
                    "status_code": r.status_code,
                    "error": err_payload.get("error", "Unknown"),
                    "message": err_payload.get("message", r.text[:60]),
                })
        except Exception:
            pass

    checked_total = len(active_clean) + len(moderation_labeled) + len(deleted_or_takedown)
    print(f"\nStatus Breakdown across {checked_total} probed authors:", flush=True)
    print(f"  - Active & Clean (Normal Humans/Accounts): {len(active_clean)}")
    print(f"  - Active but Moderation Labeled:           {len(moderation_labeled)}")
    print(f"  - Deleted / Taken Down / Suspended:        {len(deleted_or_takedown)}")

    if deleted_or_takedown:
        print("\n=== CONFIRMED TAKEDOWN / DELETED USERS ===")
        for item in deleted_or_takedown:
            print(f"  DID: {item['did']} | HTTP {item['status_code']} | Error: {item['error']} | Msg: {item['message']}")

    if moderation_labeled:
        print("\n=== ACCOUNTS WITH ACTIVE MODERATION LABELS ===")
        for item in moderation_labeled:
            print(f"  DID: {item['did']} (@{item['handle']}) | Labels: {item['labels']}")

    print("\n" + "=" * 70)
    print("AUDIT COMPLETE")
    print("=" * 70, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="auto", choices=["auto", "mongodb", "parquet"])
    args = parser.parse_args()
    audit_dataset(backend_choice=args.backend)
