"""Pilot testing utility to inspect candidate queries and check duplicate signals empirically."""

from __future__ import annotations

import argparse
from typing import List, Dict, Any
from collections import Counter
import requests


def run_pilot(queries: List[str], limit: int = 50) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "ChorusRank-Pilot/0.1.0"})
    url = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"

    print("\n" + "=" * 70)
    print("                 CHORUSRANK QUERY PILOT INSPECTION")
    print("=" * 70)

    for q in queries:
        print(f"\n--- Testing Query: '{q}' (limit={limit}) ---")
        try:
            resp = session.get(url, params={"q": q, "limit": limit}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [ERROR] Query failed: {e}")
            continue

        posts = data.get("posts", [])
        if not posts:
            print(f"  No posts returned for '{q}'.")
            continue

        authors = [p.get("author", {}).get("did") for p in posts]
        author_counts = Counter(authors)
        texts = [p.get("record", {}).get("text", "").strip() for p in posts]
        text_counts = Counter(texts)
        has_links = sum(1 for p in posts if "facets" in p.get("record", {}) and any(
            any("link" in feat.get("$type", "") for feat in facet.get("features", []))
            for facet in p.get("record", {}).get("facets", [])
        ))

        duplicate_texts = {text: count for text, count in text_counts.items() if count > 1}

        print(f"  • Total Posts Retrieved: {len(posts)}")
        print(f"  • Unique Authors:        {len(author_counts)} (Max posts by single DID: {max(author_counts.values())})")
        print(f"  • Posts with Links:      {has_links} ({has_links/len(posts)*100:.1f}%)")
        print(f"  • Exact Duplicate Texts: {len(duplicate_texts)} clusters (affecting {sum(duplicate_texts.values())} posts)")

        print("  • Sample Post Previews:")
        for i, text in enumerate(texts[:3]):
            clean_snippet = text.replace("\n", " ")[:90]
            safe_snippet = clean_snippet.encode("ascii", errors="replace").decode("ascii")
            print(f"    [{i+1}] \"{safe_snippet}...\"")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pilot test search queries on live Bluesky API")
    parser.add_argument("--query", type=str, nargs="+", default=["t.me", "telegram", "airdrop", "giveaway", "dm to recover"])
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    run_pilot(args.query, limit=args.limit)
