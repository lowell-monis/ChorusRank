"""High-performance storage manager using Parquet and DuckDB."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

import duckdb
import pandas as pd

from .schema import RawEventRecord, ProfileMetadata, AccountStatusRecord


class ChorusStorage:
    """Manages Parquet files and analytical queries via DuckDB."""

    def __init__(
        self,
        raw_dir: str = "data/raw",
        enriched_dir: str = "data/enriched",
        processed_dir: str = "data/processed",
        duckdb_path: Optional[str] = "data/chorusrank.duckdb",
    ):
        self.raw_dir = Path(raw_dir)
        self.enriched_dir = Path(enriched_dir)
        self.processed_dir = Path(processed_dir)
        self.duckdb_path = Path(duckdb_path) if duckdb_path else None

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.enriched_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        self.con = duckdb.connect(str(self.duckdb_path) if self.duckdb_path else ":memory:")
        self._init_duckdb()

    def _init_duckdb(self) -> None:
        """Create views or tables if needed."""
        pass

    def save_raw_events(self, events: List[RawEventRecord], partition_name: Optional[str] = None) -> Path:
        """Save a batch of raw events as a Parquet file."""
        if not events:
            return self.raw_dir

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        timestamp = int(datetime.now(timezone.utc).timestamp())
        part = f"_{partition_name}" if partition_name else ""
        filename = f"events_{date_str}{part}_{timestamp}.parquet"
        file_path = self.raw_dir / filename

        dicts = [e.model_dump() for e in events]
        # Serialize nested fields to JSON string for Parquet compatibility
        for d in dicts:
            if "facets" in d and d["facets"] is not None:
                d["facets"] = json.dumps(d["facets"])
            if "raw_payload" in d and d["raw_payload"] is not None:
                d["raw_payload"] = json.dumps(d["raw_payload"])
            if "langs" in d and d["langs"] is not None:
                d["langs"] = json.dumps(d["langs"])

        df = pd.DataFrame(dicts)
        df.to_parquet(file_path, index=False, engine="pyarrow")
        return file_path

    def save_profiles(self, profiles: List[ProfileMetadata]) -> Path:
        """Save enriched profile records to Parquet."""
        if not profiles:
            return self.enriched_dir

        timestamp = int(datetime.now(timezone.utc).timestamp())
        file_path = self.enriched_dir / f"profiles_{timestamp}.parquet"

        df = pd.DataFrame([p.model_dump() for p in profiles])
        df.to_parquet(file_path, index=False, engine="pyarrow")
        return file_path

    def save_status_checks(self, records: List[AccountStatusRecord]) -> Path:
        """Save longitudinal account status checks to Parquet."""
        if not records:
            return self.enriched_dir

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        timestamp = int(datetime.now(timezone.utc).timestamp())
        file_path = self.enriched_dir / f"status_checks_{date_str}_{timestamp}.parquet"

        df = pd.DataFrame([r.model_dump() for r in records])
        df.to_parquet(file_path, index=False, engine="pyarrow")
        return file_path

    def get_distinct_dids(self) -> List[str]:
        """Fetch all unique DIDs observed across all raw event Parquet files."""
        parquet_files = list(self.raw_dir.glob("*.parquet"))
        if not parquet_files:
            return []

        query = f"""
            SELECT DISTINCT did 
            FROM read_parquet('{self.raw_dir}/*.parquet')
            WHERE did IS NOT NULL
        """
        res = self.con.execute(query).fetchall()
        return [row[0] for row in res]

    def get_already_enriched_dids(self) -> set[str]:
        """Fetch all unique DIDs that already have profile metadata."""
        profile_files = list(self.enriched_dir.glob("profiles_*.parquet"))
        if not profile_files:
            return set()

        query = f"""
            SELECT DISTINCT did 
            FROM read_parquet('{self.enriched_dir}/profiles_*.parquet')
        """
        res = self.con.execute(query).fetchall()
        return {row[0] for row in res}

    def get_dataset_summary(self) -> Dict[str, Any]:
        """Generate high-level dataset metrics for the intermediate report."""
        parquet_files = list(self.raw_dir.glob("*.parquet"))
        if not parquet_files:
            return {
                "total_events": 0,
                "unique_dids": 0,
                "posts_count": 0,
                "reposts_count": 0,
                "earliest_event": None,
                "latest_event": None,
            }

        query = f"""
            SELECT 
                COUNT(*) as total_events,
                COUNT(DISTINCT did) as unique_dids,
                SUM(CASE WHEN collection LIKE '%post%' THEN 1 ELSE 0 END) as posts_count,
                SUM(CASE WHEN collection LIKE '%repost%' THEN 1 ELSE 0 END) as reposts_count,
                MIN(created_at) as earliest_event,
                MAX(created_at) as latest_event
            FROM read_parquet('{self.raw_dir}/*.parquet')
        """
        df = self.con.execute(query).df()
        return df.to_dict(orient="records")[0]
