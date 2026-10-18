"""Data schemas for ChorusRank."""

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class RawEventRecord(BaseModel):
    """Schema for a post, reply, or repost event."""
    event_id: str
    did: str
    rkey: str
    cid: Optional[str] = None
    created_at: str
    collected_at: str
    collection: str  # e.g., "app.bsky.feed.post" or "app.bsky.feed.repost"
    text: Optional[str] = None
    langs: Optional[List[str]] = Field(default_factory=list)
    reply_parent_uri: Optional[str] = None
    reply_root_uri: Optional[str] = None
    reposted_uri: Optional[str] = None
    facets: Optional[List[dict]] = Field(default_factory=list)
    query_tag: Optional[str] = None
    raw_payload: Optional[dict] = None


class ProfileMetadata(BaseModel):
    """Schema for account profile metadata."""
    did: str
    handle: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None  # from indexedAt
    followers_count: int = 0
    follows_count: int = 0
    posts_count: int = 0
    has_avatar: bool = False
    has_banner: bool = False
    enriched_at: str


class AccountStatusRecord(BaseModel):
    """Schema for longitudinal takedown & suspension checks."""
    did: str
    handle: Optional[str] = None
    status: str  # "ACTIVE", "TAKEDOWN", "DEACTIVATED", "NOT_FOUND", "ERROR"
    status_code: Optional[int] = 200
    error_message: Optional[str] = None
    checked_at: str
