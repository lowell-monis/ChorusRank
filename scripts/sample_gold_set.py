"""Stratified sampler to export candidate accounts for manual gold-set annotation."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from chorusrank.storage import get_storage, MongoStorage, ChorusStorage


def export_gold_candidates(sample_size_per_stratum: int = 150, out_path: str = "data/gold_set_sample.csv") -> None:
    storage = get_storage()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"[Gold Set Sampler] Sampling accounts across strata (target: {sample_size_per_stratum} per stratum)...")

    rows = []

    if isinstance(storage, MongoStorage):
        for stratum in ["targeted", "baseline"]:
            pipeline = [
                {"$match": {"stratum": stratum}},
                {"$sample": {"size": sample_size_per_stratum * 3}},
                {"$group": {
                    "_id": "$did",
                    "sample_text": {"$first": "$text"},
                    "query_tag": {"$first": "$query_tag"},
                    "event_count": {"$sum": 1}
                }},
                {"$limit": sample_size_per_stratum}
            ]
            results = list(storage.events_col.aggregate(pipeline))
            for r in results:
                rows.append({
                    "did": r["_id"],
                    "stratum": stratum,
                    "query_tag": r.get("query_tag", "N/A"),
                    "observed_events": r.get("event_count", 1),
                    "sample_post_text": (r.get("sample_text") or "").replace("\n", " ")[:150],
                    "gold_label": "",  # To be filled by annotator: INAUTHENTIC_COORDINATED / ORGANIC_BENIGN / UNCERTAIN
                    "annotator_id": "",
                    "evidence_notes": "",
                })
    else:
        # Parquet / DuckDB fallback
        try:
            query = f"""
                WITH ranked AS (
                    SELECT 
                        did, stratum, query_tag, text as sample_post_text,
                        COUNT(*) OVER (PARTITION BY did) as observed_events,
                        ROW_NUMBER() OVER (PARTITION BY stratum, did ORDER BY created_at) as rn
                    FROM read_parquet('{storage.raw_dir}/*.parquet')
                )
                SELECT did, stratum, query_tag, observed_events, sample_post_text
                FROM ranked
                WHERE rn = 1
                LIMIT {sample_size_per_stratum * 2}
            """
            df_sampled = storage.con.execute(query).df()
            for _, r in df_sampled.iterrows():
                rows.append({
                    "did": r["did"],
                    "stratum": r["stratum"],
                    "query_tag": r.get("query_tag", "N/A"),
                    "observed_events": r["observed_events"],
                    "sample_post_text": (r["sample_post_text"] or "").replace("\n", " ")[:150],
                    "gold_label": "",
                    "annotator_id": "",
                    "evidence_notes": "",
                })
        except Exception as e:
            print(f"[Gold Set Sampler] Error reading from local Parquet: {e}")
            return

    if not rows:
        print("[Gold Set Sampler] No records found to sample. Ensure events have been collected first.")
        return

    df = pd.DataFrame(rows)
    df.to_csv(out, index=False, encoding="utf-8")
    print(f"[Gold Set Sampler] Successfully exported {len(df)} candidate accounts to {out}")
    print(f"[Gold Set Sampler] Open {out} and consult config/labeling_rubric.md to annotate.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export candidate accounts for manual annotation")
    parser.add_argument("--size", type=int, default=150, help="Sample size per stratum")
    parser.add_argument("--out", type=str, default="data/gold_set_sample.csv")
    args = parser.parse_args()

    export_gold_candidates(sample_size_per_stratum=args.size, out_path=args.out)
