"""Data schemas for ChorusRank.

Designed for comprehensive multimodal analysis:
- Graph mining (co-activity, interaction, reply, quote, repost, domain-sharing)
- NLP & text similarity (MinHash/LSH, n-grams, lexical entropy, embeddings)
- Longitudinal tracking & multi-column moderation label recoding
"""

from __future__ import annotations

from typing import Any, List, Optional, Dict
from pydantic import BaseModel, Field


class RawEventRecord(BaseModel):
    """Schema for a post, reply, or repost event with complete graph & content context."""
    event_id: str
    did: str
    handle: Optional[str] = None
    rkey: str
    cid: Optional[str] = None
    created_at: str
    collected_at: str
    collection: str  # e.g., "app.bsky.feed.post" or "app.bsky.feed.repost"
    text: Optional[str] = None
    clean_text: Optional[str] = None  # Lowercased, URLs/handles stripped for LSH/NLP
    langs: Optional[List[str]] = Field(default_factory=list)
    
    # Entity & NLP extraction
    urls: Optional[List[str]] = Field(default_factory=list)
    domains: Optional[List[str]] = Field(default_factory=list)
    tags: Optional[List[str]] = Field(default_factory=list)
    mentions: Optional[List[str]] = Field(default_factory=list)  # DIDs mentioned
    
    # Explicit numeric counts & text metrics (ready for ML classifiers & fast indexing)
    char_length: int = 0
    word_count: int = 0
    url_count: int = 0
    domain_count: int = 0
    tag_count: int = 0
    mention_count: int = 0
    
    # Interaction & Graph edges
    reply_parent_uri: Optional[str] = None
    reply_parent_did: Optional[str] = None  # Direct edge for reply interaction graph
    reply_root_uri: Optional[str] = None
    reply_root_did: Optional[str] = None    # Direct edge for thread interaction graph
    quote_uri: Optional[str] = None
    quote_did: Optional[str] = None        # Direct edge for quote interaction graph
    reposted_uri: Optional[str] = None
    reposted_did: Optional[str] = None     # Direct edge for amplification graph
    
    # Raw structures
    facets: Optional[List[dict]] = Field(default_factory=list)
    embed: Optional[dict] = None
    
    # Sampling metadata
    stratum: str = "targeted"  # "targeted" (search queries) or "baseline" (sampled firehose)
    query_tag: Optional[str] = None
    post_uri: Optional[str] = None  # Full AT-URI (at://did/app.bsky.feed.post/rkey)
    
    # Engagement & Lifecycle metrics
    like_count: Optional[int] = 0
    repost_count: Optional[int] = 0
    reply_count: Optional[int] = 0
    quote_count: Optional[int] = 0
    is_deleted: Optional[bool] = False
    last_checked_at: Optional[str] = None
    
    # Post-level moderation labels
    post_labels: Optional[List[dict]] = Field(default_factory=list)
    post_label_values: Optional[List[str]] = Field(default_factory=list)
    author_labels: Optional[List[dict]] = Field(default_factory=list)
    author_label_values: Optional[List[str]] = Field(default_factory=list)
    
    # Complete raw payload (zero data loss guarantee)
    raw_payload: Optional[dict] = None


class ProfileMetadata(BaseModel):
    """Schema for account profile metadata with deep moderation label flags."""
    did: str
    handle: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None  # from indexedAt or createdAt
    followers_count: int = 0
    follows_count: int = 0
    posts_count: int = 0
    has_avatar: bool = False
    has_banner: bool = False
    
    # Raw and parsed moderation labels
    raw_labels: Optional[List[dict]] = Field(default_factory=list)
    label_values: Optional[List[str]] = Field(default_factory=list)
    label_sources: Optional[List[str]] = Field(default_factory=list)
    has_moderation_labels: bool = False
    
    # Granular label flags for multi-column recoding
    label_flag_suspend: bool = False
    label_flag_takedown: bool = False
    label_flag_spam: bool = False
    label_flag_warn: bool = False
    label_flag_hide: bool = False
    label_flag_bot: bool = False
    is_self_labeled: bool = False
    is_self_declared_bot: bool = False
    
    enriched_at: str
    raw_profile: Optional[dict] = None


class AccountStatusRecord(BaseModel):
    """Schema for longitudinal takedown & suspension checks with multi-column recoding."""
    did: str
    handle: Optional[str] = None
    status: str  # "ACTIVE", "TAKEDOWN", "DEACTIVATED", "NOT_FOUND", "RATE_LIMITED", "UNRECOGNIZED_ERROR", "NETWORK_ERROR"
    status_code: Optional[int] = 200
    error_name: Optional[str] = None
    error_message: Optional[str] = None
    raw_error: Optional[dict] = None  # Preserves full payload to avoid silent misclassification
    
    # Active account labels (if status_code == 200)
    raw_labels: Optional[List[dict]] = Field(default_factory=list)
    label_values: Optional[List[str]] = Field(default_factory=list)
    
    # Multi-column recoded alternatives for flexible experimental evaluation:
    # 1. Strict official platform moderation action
    label_strict_takedown: bool = False
    # 2. Broad inauthentic proxy (takedown + deactivation + suspend/spam labels)
    label_broad_inauthentic: bool = False
    # 3. Voluntary churn (self-deactivations)
    label_churn: bool = False
    is_self_declared_bot: bool = False
    is_bot_labeled: bool = False
    # 4. Multiclass status category
    label_category: str = "ACTIVE_CLEAN"  # ACTIVE_CLEAN, ACTIVE_LABELED, TAKEDOWN, DEACTIVATED, PURGED_OR_MISSING, UNKNOWN
    
    checked_at: str


class GoldLabelRecord(BaseModel):
    """Schema for hand-labeled gold evaluation set."""
    did: str
    handle: Optional[str] = None
    gold_label: str  # "INAUTHENTIC_COORDINATED", "ORGANIC_BENIGN", "UNCERTAIN"
    annotator_id: str
    sample_stratum: str  # "targeted" or "baseline"
    evidence_notes: Optional[str] = None
    labeled_at: str
