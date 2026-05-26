from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class FeedSource:
    name: str
    entrypoint: str
    source_type: str  # creator / official / trend
    entry_type: str = "rss_atom"  # rss_atom / sitemap / manual_source
    category: str = ""
    region: str = "global"
    priority: int = 0
    enabled: bool = True
    pending: bool = False
    fallbacks: list[str] | None = None
    manual_items: list[dict[str, Any]] | None = None
    source_id: str = ""

    @property
    def url(self) -> str:
        # Backward-compatible alias for code paths still using `url`.
        return self.entrypoint

    @property
    def is_creator(self) -> bool:
        return self.source_type == "creator"


@dataclass
class FeedItem:
    title: str
    link: str
    published_at: datetime
    source_name: str
    source_url: str
    source_type: str
    summary: str = ""
    pinned: bool = False
    source_region: str = "global"
    source_priority: int = 0
    source_health: float = 1.0
    source_id: str = ""
    cluster_id: str = ""

    @property
    def date_label(self) -> str:
        return self.published_at.strftime("%Y-%m-%d")
