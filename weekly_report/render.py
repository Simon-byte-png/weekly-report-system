from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

from .editorial import build_editorial_item
from .models import FeedItem


KEYWORD_RULES = [
    (["agent", "sdk", "codex", "cli"], "执行层升级"),
    (["opus", "gemini", "model", "模型", "embedding"], "模型能力跃迁"),
    (["chrome", "browser", "app", "workspace", "入口"], "入口层变化"),
    (["腾讯", "百度", "阿里", "qwen", "千帆", "deepseek"], "中国市场落地"),
]

AI_INCLUDE_KEYWORDS = [
    "ai",
    "agent",
    "agents",
    "model",
    "models",
    "llm",
    "gpt",
    "claude",
    "gemini",
    "codex",
    "sdk",
    "copilot",
    "deepseek",
    "qwen",
    "hunyuan",
    "mcp",
    "embedding",
    "千帆",
    "向量",
    "智能",
    "生成",
    "文生图",
    "多模态",
    "大模型",
    "提示词",
]

AI_EXCLUDE_KEYWORDS = [
    "travel",
    "summer travel",
    "vacation",
    "汽车销量",
    "体育",
    "电影票房",
]

_STOPWORDS = {
    "the",
    "a",
    "an",
    "for",
    "to",
    "and",
    "of",
    "in",
    "on",
    "with",
    "is",
    "are",
    "how",
    "why",
    "what",
    "this",
    "that",
    "from",
    "into",
    "about",
    "you",
    "your",
    "our",
    "we",
    "new",
    "now",
}


def score_item(item: FeedItem, source_health_threshold: float) -> float:
    title_lower = item.title.lower()
    score = 0.0

    if item.pinned:
        score += 120

    if item.source_type == "creator":
        score += 35
    elif item.source_type == "official":
        score += 20
    else:
        score += 10

    score += max(item.source_priority, 0) * 0.8

    for words, _ in KEYWORD_RULES:
        if any(word.lower() in title_lower for word in words):
            score += 18

    if item.source_health < source_health_threshold:
        score -= 25

    if item.source_region.lower() in {"cn", "us"}:
        score += 4

    score += min(len(item.title) / 20, 8)
    return score


def is_ai_relevant(item: FeedItem) -> bool:
    haystack = f"{item.title} {item.summary}".lower()
    if any(word in haystack for word in AI_EXCLUDE_KEYWORDS):
        return False
    return any(word in haystack for word in AI_INCLUDE_KEYWORDS) or item.pinned


def select_items(
    items: list[FeedItem],
    main_limit: int,
    quick_limit: int,
    require_official_for_main: bool,
    *,
    main_from_creators_min_ratio: float = 0.6,
    max_items_per_topic: int = 1,
    max_items_per_domain: int = 2,
    source_health_threshold: float = 0.35,
    require_region_balance: bool = True,
) -> tuple[list[FeedItem], list[FeedItem]]:
    relevant = [item for item in items if is_ai_relevant(item)]

    clusters = _cluster_items(relevant)
    representatives: list[FeedItem] = []
    for cluster_id, cluster_items in enumerate(clusters, start=1):
        ranked_cluster = sorted(
            cluster_items,
            key=lambda x: score_item(x, source_health_threshold),
            reverse=True,
        )
        top = ranked_cluster[0]
        top.cluster_id = f"topic-{cluster_id}"
        representatives.append(top)

    ranked = sorted(
        representatives,
        key=lambda x: score_item(x, source_health_threshold),
        reverse=True,
    )

    creator_target = min(
        main_limit,
        max(0, math.ceil(main_limit * max(main_from_creators_min_ratio, 0.0))),
    )

    selected: list[FeedItem] = []
    selected_clusters: set[str] = set()
    domain_counts: dict[str, int] = defaultdict(int)

    def can_add(item: FeedItem) -> bool:
        if item.cluster_id in selected_clusters:
            return False
        if require_official_for_main and item.source_type != "official":
            return False
        if domain_counts[_domain(item.link)] >= max_items_per_domain:
            return False
        return True

    # Pass 1: ensure creator coverage.
    for item in ranked:
        if len(selected) >= main_limit:
            break
        if _creator_count(selected) >= creator_target:
            break
        if item.source_type != "creator":
            continue
        if not can_add(item):
            continue
        selected.append(item)
        selected_clusters.add(item.cluster_id)
        domain_counts[_domain(item.link)] += 1

    # Pass 2: fill remaining slots from all candidates.
    for item in ranked:
        if len(selected) >= main_limit:
            break
        if not can_add(item):
            continue
        selected.append(item)
        selected_clusters.add(item.cluster_id)
        domain_counts[_domain(item.link)] += 1

    if require_region_balance:
        selected = _enforce_cn_us_balance(
            selected=selected,
            ranked=ranked,
            selected_clusters=selected_clusters,
            domain_counts=domain_counts,
            max_items_per_domain=max_items_per_domain,
        )

    # Main list by final score.
    selected = sorted(
        selected,
        key=lambda x: score_item(x, source_health_threshold),
        reverse=True,
    )[:main_limit]

    # Quick list: one representative per remaining topic.
    quick: list[FeedItem] = []
    for item in ranked:
        if len(quick) >= quick_limit:
            break
        if item.cluster_id in {x.cluster_id for x in selected}:
            continue
        quick.append(item)

    # Fallback: include other relevant items if topic reps are not enough.
    if len(quick) < quick_limit:
        ranked_all = sorted(
            relevant,
            key=lambda x: score_item(x, source_health_threshold),
            reverse=True,
        )
        seen_links = {item.link for item in quick}
        for item in ranked_all:
            if len(quick) >= quick_limit:
                break
            if item.cluster_id in {x.cluster_id for x in selected}:
                continue
            if item.link in seen_links:
                continue
            quick.append(item)
            seen_links.add(item.link)

    # max_items_per_topic currently represented by cluster selection (1 topic -> 1 rep).
    _ = max_items_per_topic
    return selected, quick


def render_report(
    *,
    end_date: datetime,
    main_items: list[FeedItem],
    quick_items: list[FeedItem],
    title_prefix: str,
    language: str = "zh-CN",
    show_source: bool = False,
    editorial_mode: str = "viewpoint",
    max_headline_chars: int = 32,
    max_body_chars: int = 180,
    prevent_verbatim_chars: int = 30,
) -> str:
    title = f"[{end_date.year}年{end_date.month}月{end_date.day}日 {title_prefix}]"
    lines: list[str] = [title, ""]

    for idx, item in enumerate(main_items, start=1):
        lines.extend(
            _render_main_item(
                idx,
                item,
                language=language,
                show_source=show_source,
                editorial_mode=editorial_mode,
                max_headline_chars=max_headline_chars,
                max_body_chars=max_body_chars,
                prevent_verbatim_chars=prevent_verbatim_chars,
            )
        )
        lines.append("")

    lines.append("快讯")
    for idx, item in enumerate(quick_items, start=1):
        quick_title = _render_quick_title(
            item,
            language=language,
            editorial_mode=editorial_mode,
            max_headline_chars=max_headline_chars,
            max_body_chars=max_body_chars,
            prevent_verbatim_chars=prevent_verbatim_chars,
        )
        lines.append(f"{idx}. {quick_title}（{item.date_label}）")
        lines.append(f"链接：{item.link}")
        lines.append("")

    if len(quick_items) == 0:
        lines.append("1. 本周快讯为空（未检测到满足规则的候选）。")

    return "\n".join(lines).strip() + "\n"


def _render_main_item(
    idx: int,
    item: FeedItem,
    *,
    language: str,
    show_source: bool,
    editorial_mode: str,
    max_headline_chars: int,
    max_body_chars: int,
    prevent_verbatim_chars: int,
) -> list[str]:
    if editorial_mode == "viewpoint":
        editorial = build_editorial_item(
            item,
            language=language,
            max_headline_chars=max_headline_chars,
            max_body_chars=max_body_chars,
            prevent_verbatim_chars=prevent_verbatim_chars,
        )
        lines = [
            f"{idx}）{editorial.headline}",
            editorial.body,
            f"链接：{item.link}",
        ]
        if show_source:
            source_type_text = "创作者" if item.source_type == "creator" else ("官方" if item.source_type == "official" else "趋势")
            lines.append(f"来源：{source_type_text}来源（{item.source_name}）")
        return lines

    local_title = _localize_title(item.title, language)
    theme = _infer_theme(local_title)
    date_text = item.date_label

    conclusion = f"在 {date_text}，业内对“{local_title}”的讨论明显升温。"
    importance = _importance_text(theme)
    product_judgement = _product_judgement_text(theme)

    lines = [
        f"{idx}）{local_title}",
        conclusion,
        f"为什么重要：{importance}",
        f"产品判断：{product_judgement}",
        f"链接：{item.link}",
    ]

    if show_source:
        source_type_text = "创作者" if item.source_type == "creator" else ("官方" if item.source_type == "official" else "趋势")
        lines.append(f"来源：{source_type_text}来源（{item.source_name}）")
    return lines


def _render_quick_title(
    item: FeedItem,
    *,
    language: str,
    editorial_mode: str,
    max_headline_chars: int,
    max_body_chars: int,
    prevent_verbatim_chars: int,
) -> str:
    if editorial_mode == "viewpoint":
        editorial = build_editorial_item(
            item,
            language=language,
            max_headline_chars=max_headline_chars,
            max_body_chars=max_body_chars,
            prevent_verbatim_chars=prevent_verbatim_chars,
        )
        return editorial.quick_title
    return _localize_title(item.title, language)


def _cluster_items(items: list[FeedItem]) -> list[list[FeedItem]]:
    clusters: list[list[FeedItem]] = []
    token_cache: dict[str, set[str]] = {}

    ranked = sorted(items, key=lambda x: len(x.title), reverse=True)
    for item in ranked:
        tokens = _title_tokens(item.title)
        token_cache[item.link] = tokens
        placed = False

        for cluster in clusters:
            anchor = cluster[0]
            if _same_topic(item, anchor, tokens, token_cache.get(anchor.link, _title_tokens(anchor.title))):
                cluster.append(item)
                placed = True
                break

        if not placed:
            clusters.append([item])

    return clusters


def _same_topic(item_a: FeedItem, item_b: FeedItem, tokens_a: set[str], tokens_b: set[str]) -> bool:
    if _normalized_link(item_a.link) == _normalized_link(item_b.link):
        return True

    if item_a.source_name == item_b.source_name and _domain(item_a.link) == _domain(item_b.link):
        if len(tokens_a.intersection(tokens_b)) >= 3:
            return True

    if not tokens_a or not tokens_b:
        return False

    intersection = len(tokens_a.intersection(tokens_b))
    union = len(tokens_a.union(tokens_b))
    if union == 0:
        return False
    jaccard = intersection / union

    return jaccard >= 0.55 or intersection >= 4


def _title_tokens(title: str) -> set[str]:
    text = title.lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    parts = [p for p in text.split() if len(p) >= 2 and p not in _STOPWORDS]
    return set(parts)


def _normalized_link(link: str) -> str:
    clean = re.sub(r"#.*$", "", link.strip())
    clean = re.sub(r"\?.*$", "", clean)
    return clean.rstrip("/")


def _domain(link: str) -> str:
    return urlparse(link).netloc.lower()


def _creator_count(items: list[FeedItem]) -> int:
    return sum(1 for item in items if item.source_type == "creator")


def _enforce_cn_us_balance(
    *,
    selected: list[FeedItem],
    ranked: list[FeedItem],
    selected_clusters: set[str],
    domain_counts: dict[str, int],
    max_items_per_domain: int,
) -> list[FeedItem]:
    if not selected:
        return selected

    regions = {item.source_region.lower() for item in selected}
    needed = [region for region in ("cn", "us") if region not in regions]
    if not needed:
        return selected

    for missing_region in needed:
        replacement = None
        for candidate in ranked:
            if candidate.source_region.lower() != missing_region:
                continue
            if candidate.cluster_id in selected_clusters:
                continue
            if domain_counts[_domain(candidate.link)] >= max_items_per_domain:
                continue
            replacement = candidate
            break

        if replacement is None:
            continue

        # Replace the weakest non-missing-region item.
        non_region = [item for item in selected if item.source_region.lower() != missing_region]
        if not non_region:
            continue

        weakest = sorted(non_region, key=lambda x: x.source_priority)[0]
        selected.remove(weakest)
        selected.append(replacement)
        selected_clusters.add(replacement.cluster_id)
        domain_counts[_domain(replacement.link)] += 1

    return selected


_TITLE_REPLACEMENTS = [
    (r"^\[AINews\]\s*", "AI快讯："),
    (r"\bAI Mode in Chrome\b", "Chrome 的 AI 模式"),
    (r"\bCodex for \(almost\) everything\b", "Codex 全栈能力更新"),
    (r"\bThe next evolution of the Agents SDK\b", "Agents SDK 新一轮演进"),
    (r"\bworkspace agents\b", "工作空间智能体"),
    (r"\bOpen Model\b", "开源模型"),
    (r"\bFlagship-Level Coding\b", "旗舰级编码能力"),
    (r"\bagentic\b", "智能体化"),
    (r"\bdeep research\b", "深度研究"),
    (r"\bintroducing\b", "发布"),
    (r"\blaunch(?:ed|es)?\b", "发布"),
    (r"\bupdate(?:d)?\b", "更新"),
    (r"\bEverything You Need to Know About\b", "全面解读"),
    (r"\bwith\b", "与"),
]


def _localize_title(title: str, language: str) -> str:
    text = title.strip()
    if not text or language != "zh-CN":
        return text

    localized = text
    for pattern, repl in _TITLE_REPLACEMENTS:
        localized = re.sub(pattern, repl, localized, flags=re.IGNORECASE)

    localized = re.sub(r"\s+", " ", localized).strip(" -:;,.")
    # If mixed language is still too heavy in English, switch to a Chinese frame
    # and keep only key model/product entities.
    if _ascii_ratio(localized) > 0.25:
        entities = _extract_entities(title)
        if entities:
            return "AI动态：" + "、".join(entities[:4])
        return "AI动态更新"

    if _has_chinese(localized):
        return localized
    return "AI动态：" + localized


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _ascii_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    ascii_letters = sum(1 for c in chars if ("a" <= c.lower() <= "z"))
    return ascii_letters / len(chars)


def _extract_entities(text: str) -> list[str]:
    # Keep model/product names and versions as-is, while converting sentence frame to Chinese.
    entities = re.findall(r"[A-Za-z][A-Za-z0-9+_.-]{1,30}", text)
    allow_terms = {
        "ai",
        "gpt",
        "claude",
        "gemini",
        "codex",
        "qwen",
        "deepseek",
        "kimi",
        "opus",
        "sdk",
        "api",
        "llm",
        "mcp",
        "openai",
        "anthropic",
        "moonshot",
        "shopify",
        "agent",
        "agents",
    }
    blocked_terms = {
        "my",
        "your",
        "the",
        "and",
        "for",
        "with",
        "world",
        "worlds",
        "leading",
        "need",
        "know",
        "everything",
        "about",
        "refreshes",
        "catch",
        "ahead",
        "drew",
        "better",
        "laptop",
        "week",
        "opinion",
    }
    keep: list[str] = []
    seen = set()
    for token in entities:
        lower = token.lower()
        if lower in _STOPWORDS:
            continue
        if lower in blocked_terms:
            continue
        has_digit = any(ch.isdigit() for ch in token)
        looks_like_name = token[0].isupper()
        is_allow = lower in allow_terms
        if not (has_digit or looks_like_name or is_allow):
            continue
        if token not in seen:
            keep.append(token)
            seen.add(token)
    return keep


def _infer_theme(title: str) -> str:
    lower = title.lower()
    for words, theme in KEYWORD_RULES:
        if any(word.lower() in lower for word in words):
            return theme
    return "生态常规更新"


def _importance_text(theme: str) -> str:
    mapping = {
        "执行层升级": "这说明 AI 产品正在从“回答问题”走向“直接执行任务”，对开发者和企业流程影响更直接。",
        "模型能力跃迁": "模型上限提高后，会直接影响生产效率、内容质量和复杂任务可交付性。",
        "入口层变化": "入口一旦变化，用户行为和分发格局会跟着变化，影响远大于单次功能更新。",
        "中国市场落地": "国内厂商把能力做成可接入的产品接口，会明显降低企业落地成本。",
        "生态常规更新": "这是平台能力演进的一部分，短期影响有限，但会持续改变工具链选择。",
    }
    return mapping[theme]


def _product_judgement_text(theme: str) -> str:
    mapping = {
        "执行层升级": "后续竞争重点会从模型参数转向“执行闭环、权限控制和工具编排”。",
        "模型能力跃迁": "接下来产品分层会更明显：基础模型能力 + 行业工作流封装。",
        "入口层变化": "谁占据高频入口，谁就更容易建立 AI 工作流标准。",
        "中国市场落地": "未来 1-2 个季度会继续出现“文档更新驱动真实采用”的信号。",
        "生态常规更新": "建议持续观察后续版本节奏，确认是否形成稳定产品方向。",
    }
    return mapping[theme]
