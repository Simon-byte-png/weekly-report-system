from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from zoneinfo import ZoneInfo

from .models import FeedItem, FeedSource

_HTTP_TIMEOUT = 20
_USER_AGENT = "weekly-report-bot/1.0"


@dataclass
class SourceFetchStatus:
    source_id: str
    source_name: str
    source_type: str
    source_region: str
    success: bool
    used_endpoint: str
    error: str = ""
    total_items: int = 0
    recent_7d: int = 0
    recent_30d: int = 0
    health: float = 0.0


def load_sources(config_path: Path) -> tuple[list[FeedSource], dict]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    source_cfg = raw.get("sources", {})

    sources: list[FeedSource] = []

    for row in source_cfg.get("creators", []):
        source = _build_source(
            row,
            default_source_type="creator",
            default_entry_type="rss_atom",
        )
        if source is not None and source.enabled:
            sources.append(source)

    # Backward compatibility: keep official/trend support.
    for row in source_cfg.get("official", []):
        source = _build_source(
            row,
            default_source_type="official",
            default_entry_type="rss_atom",
        )
        if source is not None and source.enabled:
            sources.append(source)

    for row in source_cfg.get("trend", []):
        source = _build_source(
            row,
            default_source_type="trend",
            default_entry_type="rss_atom",
        )
        if source is not None and source.enabled:
            sources.append(source)

    return sources, raw


def _build_source(
    row: dict,
    *,
    default_source_type: str,
    default_entry_type: str,
) -> FeedSource | None:
    name = str(row.get("name", "")).strip()
    entrypoint = str(row.get("entrypoint", row.get("url", ""))).strip()
    if not name or not entrypoint:
        return None

    source_type = str(row.get("source_type", default_source_type)).strip() or default_source_type
    entry_type = str(row.get("entry_type", default_entry_type)).strip() or default_entry_type

    fallbacks = [str(x).strip() for x in row.get("fallback", row.get("fallbacks", [])) if str(x).strip()]
    source_id = str(row.get("source_id", "")).strip() or _slugify(name)

    manual_items = row.get("manual_items")
    if not isinstance(manual_items, list):
        manual_items = []

    return FeedSource(
        name=name,
        entrypoint=entrypoint,
        source_type=source_type,
        entry_type=entry_type,
        category=str(row.get("category", "")).strip(),
        region=str(row.get("region", "global")).strip() or "global",
        priority=int(row.get("priority", 0)),
        enabled=bool(row.get("enabled", True)),
        pending=bool(row.get("pending", False)),
        fallbacks=fallbacks,
        manual_items=manual_items,
        source_id=source_id,
    )


def load_manual_items(raw_config: dict, tz: ZoneInfo) -> list[FeedItem]:
    # Global fallback manual items for backward compatibility.
    items: list[FeedItem] = []
    for row in raw_config.get("manual_items", []):
        item = _manual_item_to_feed_item(
            row=row,
            tz=tz,
            default_source_type=str(row.get("source_type", "official") or "official"),
            default_source_name=str(row.get("source_name", "Manual")).strip() or "Manual",
            default_source_url=str(row.get("source_url", row.get("link", ""))).strip(),
            source_region=str(row.get("region", "global") or "global"),
            source_priority=int(row.get("priority", 0) or 0),
            source_id=str(row.get("source_id", _slugify(str(row.get("source_name", "manual"))))),
        )
        if item is not None:
            items.append(item)

    return items


def fetch_source_items_with_status(
    source: FeedSource,
    tz: ZoneInfo,
    now: datetime,
) -> tuple[list[FeedItem], SourceFetchStatus]:
    endpoints = [source.entrypoint] + list(source.fallbacks or [])
    last_error = ""

    for endpoint in endpoints:
        try:
            items = _fetch_by_entry_type(source, endpoint, tz)
            items = _apply_source_meta(items, source)
            status = _build_status(source, items, now, True, endpoint, "")
            return items, status
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

    status = _build_status(source, [], now, False, source.entrypoint, last_error)
    return [], status


def fetch_source_items(source: FeedSource, tz: ZoneInfo) -> list[FeedItem]:
    # Backward compatible wrapper.
    items, _ = fetch_source_items_with_status(
        source=source,
        tz=tz,
        now=datetime.now(tz),
    )
    return items


def _fetch_by_entry_type(source: FeedSource, endpoint: str, tz: ZoneInfo) -> list[FeedItem]:
    entry_type = source.entry_type
    if entry_type == "manual_source":
        return _parse_manual_source_items(source, tz)
    if entry_type == "sitemap":
        return _parse_sitemap(endpoint, source, tz)

    # Default rss/atom parser.
    return _parse_feed(endpoint, source, tz)


def _parse_feed(endpoint: str, source: FeedSource, tz: ZoneInfo) -> list[FeedItem]:
    request = Request(endpoint, headers={"User-Agent": _USER_AGENT})
    with urlopen(request, timeout=_HTTP_TIMEOUT) as response:
        xml_bytes = response.read()

    root = ET.fromstring(xml_bytes)
    tag = _strip_ns(root.tag).lower()

    if tag == "rss":
        return _parse_rss(root, source, tz)
    if tag == "feed":
        return _parse_atom(root, source, tz)

    # Some endpoints return html when feed is blocked.
    raise ValueError(f"Unsupported feed root tag: {tag}")


def _parse_sitemap(endpoint: str, source: FeedSource, tz: ZoneInfo) -> list[FeedItem]:
    request = Request(endpoint, headers={"User-Agent": _USER_AGENT})
    with urlopen(request, timeout=_HTTP_TIMEOUT) as response:
        xml_bytes = response.read()

    root = ET.fromstring(xml_bytes)
    ns = _discover_namespace(root.tag)
    url_tag = f"{{{ns}}}url" if ns else "url"
    loc_tag = f"{{{ns}}}loc" if ns else "loc"
    last_tag = f"{{{ns}}}lastmod" if ns else "lastmod"

    items: list[FeedItem] = []
    for node in root.findall(url_tag):
        loc = (node.findtext(loc_tag) or "").strip()
        if not loc:
            continue

        # Keep article-like pages for sitemap sources.
        path = urlparse(loc).path.lower()
        if not any(key in path for key in ("/article", "/articles", "/insights", "/blog", "/post")):
            continue

        pub = (node.findtext(last_tag) or "").strip()
        parsed = _parse_datetime(pub, tz)
        if parsed is None:
            continue

        title = _title_from_url(loc)
        items.append(
            FeedItem(
                title=title,
                link=loc,
                published_at=parsed,
                source_name=source.name,
                source_url=endpoint,
                source_type=source.source_type,
                summary=f"Sitemap entry from {source.name}",
            )
        )

    return items


def _parse_manual_source_items(source: FeedSource, tz: ZoneInfo) -> list[FeedItem]:
    items: list[FeedItem] = []
    for row in source.manual_items or []:
        item = _manual_item_to_feed_item(
            row=row,
            tz=tz,
            default_source_type=source.source_type,
            default_source_name=source.name,
            default_source_url=source.entrypoint,
            source_region=source.region,
            source_priority=source.priority,
            source_id=source.source_id,
        )
        if item is not None:
            items.append(item)

    return items


def _manual_item_to_feed_item(
    *,
    row: dict,
    tz: ZoneInfo,
    default_source_type: str,
    default_source_name: str,
    default_source_url: str,
    source_region: str,
    source_priority: int,
    source_id: str,
) -> FeedItem | None:
    dt = _parse_datetime(str(row.get("date", "")), tz)
    if dt is None:
        return None

    title = str(row.get("title", "")).strip()
    link = str(row.get("link", "")).strip()
    if not title or not link:
        return None

    source_name = str(row.get("source_name", default_source_name)).strip() or default_source_name
    source_url = str(row.get("source_url", default_source_url)).strip() or default_source_url
    source_type = str(row.get("source_type", default_source_type)).strip() or default_source_type

    return FeedItem(
        title=title,
        link=link,
        published_at=dt,
        source_name=source_name,
        source_url=source_url,
        source_type=source_type,
        summary=str(row.get("summary", "")).strip(),
        pinned=bool(row.get("pinned", False)),
        source_region=str(row.get("region", source_region)).strip() or source_region,
        source_priority=int(row.get("priority", source_priority) or source_priority),
        source_id=str(row.get("source_id", source_id)).strip() or source_id,
    )


def _parse_rss(root: ET.Element, source: FeedSource, tz: ZoneInfo) -> list[FeedItem]:
    channel = root.find("channel")
    if channel is None:
        return []

    items: list[FeedItem] = []
    for item in channel.findall("item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        pub = _text(item.find("pubDate")) or _text(item.find("date")) or _text(item.find("published"))
        summary = _text(item.find("description"))

        parsed = _parse_datetime(pub, tz)
        if not title or not link or parsed is None:
            continue

        items.append(
            FeedItem(
                title=_clean_text(title),
                link=link.strip(),
                published_at=parsed,
                source_name=source.name,
                source_url=source.entrypoint,
                source_type=source.source_type,
                summary=_clean_text(summary),
            )
        )

    return items


def _parse_atom(root: ET.Element, source: FeedSource, tz: ZoneInfo) -> list[FeedItem]:
    ns = _discover_namespace(root.tag)
    entry_tag = f"{{{ns}}}entry" if ns else "entry"
    title_tag = f"{{{ns}}}title" if ns else "title"
    link_tag = f"{{{ns}}}link" if ns else "link"
    updated_tag = f"{{{ns}}}updated" if ns else "updated"
    published_tag = f"{{{ns}}}published" if ns else "published"
    summary_tag = f"{{{ns}}}summary" if ns else "summary"
    content_tag = f"{{{ns}}}content" if ns else "content"

    items: list[FeedItem] = []

    for entry in root.findall(entry_tag):
        title = _text(entry.find(title_tag))
        link = _atom_link(entry, link_tag)
        pub = _text(entry.find(published_tag)) or _text(entry.find(updated_tag))
        summary = _text(entry.find(summary_tag)) or _text(entry.find(content_tag))

        parsed = _parse_datetime(pub, tz)
        if not title or not link or parsed is None:
            continue

        items.append(
            FeedItem(
                title=_clean_text(title),
                link=link.strip(),
                published_at=parsed,
                source_name=source.name,
                source_url=source.entrypoint,
                source_type=source.source_type,
                summary=_clean_text(summary),
            )
        )

    return items


def iter_window_items(
    all_items: Iterable[FeedItem],
    start_dt: datetime,
    end_dt: datetime,
) -> list[FeedItem]:
    dedupe: dict[str, FeedItem] = {}
    for item in all_items:
        if item.published_at < start_dt or item.published_at > end_dt:
            continue

        key = canonical_link(item.link)
        best = dedupe.get(key)
        if best is None:
            dedupe[key] = item
            continue

        if item.pinned and not best.pinned:
            dedupe[key] = item
            continue

        if item.pinned == best.pinned and item.published_at > best.published_at:
            dedupe[key] = item

    return sorted(dedupe.values(), key=lambda i: i.published_at, reverse=True)


def canonical_link(link: str) -> str:
    clean = link.strip()
    clean = re.sub(r"#.*$", "", clean)
    clean = re.sub(r"\?.*$", "", clean)
    if clean.endswith("/") and len(clean) > len("https://a.b/"):
        clean = clean.rstrip("/")
    return clean


def _apply_source_meta(items: list[FeedItem], source: FeedSource) -> list[FeedItem]:
    for item in items:
        item.source_region = source.region
        item.source_priority = source.priority
        item.source_id = source.source_id
    return items


def _build_status(
    source: FeedSource,
    items: list[FeedItem],
    now: datetime,
    success: bool,
    used_endpoint: str,
    error: str,
) -> SourceFetchStatus:
    recent_7d = 0
    recent_30d = 0
    start_7d = now - timedelta(days=7)
    start_30d = now - timedelta(days=30)
    for item in items:
        if item.published_at >= start_7d:
            recent_7d += 1
        if item.published_at >= start_30d:
            recent_30d += 1

    availability = 1.0 if recent_7d > 0 else 0.0
    activity = min(recent_30d / 5.0, 1.0)
    success_score = 1.0 if success else 0.0
    health = round(0.4 * availability + 0.3 * activity + 0.3 * success_score, 3)

    return SourceFetchStatus(
        source_id=source.source_id,
        source_name=source.name,
        source_type=source.source_type,
        source_region=source.region,
        success=success,
        used_endpoint=used_endpoint,
        error=error,
        total_items=len(items),
        recent_7d=recent_7d,
        recent_30d=recent_30d,
        health=health,
    )


def _title_from_url(link: str) -> str:
    path = urlparse(link).path.strip("/")
    slug = path.split("/")[-1] if path else link
    slug = slug.replace("-", " ").replace("_", " ").strip()
    if not slug:
        return link
    return slug.title()


def _slugify(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "source"


def _atom_link(entry: ET.Element, link_tag: str) -> str:
    for node in entry.findall(link_tag):
        rel = (node.attrib.get("rel") or "").lower()
        href = node.attrib.get("href")
        if href and rel in ("", "alternate"):
            return href

    first = entry.find(link_tag)
    if first is not None:
        return first.attrib.get("href", "")

    return ""


def _parse_datetime(raw: str, tz: ZoneInfo) -> datetime | None:
    if not raw:
        return None

    value = raw.strip()
    # Some feeds use extra spaces before timezone: "2026-04-23 09:26:23  +0800"
    value = re.sub(r"\s+([+-]\d{4})$", r" \1", value)

    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except (TypeError, ValueError):
        pass

    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except ValueError:
        return None


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _discover_namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()) if len(node) else (node.text or "")


def _clean_text(text: str) -> str:
    if not text:
        return ""
    value = html.unescape(text)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()
