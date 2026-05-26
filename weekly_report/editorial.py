from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .models import FeedItem


@dataclass(frozen=True)
class EditorialItem:
    headline: str
    body: str
    quick_title: str


_PRODUCT_PATTERNS = [
    r"GPT[- ]?image[- ]?\d+(?:\.\d+)?",
    r"GPT[- ]?\d+(?:\.\d+)?(?:[- ][A-Za-z0-9.]+)?",
    r"Claude Code",
    r"Claude(?:\s+(?:Opus|Sonnet|Haiku))?(?:\s+\d+(?:\.\d+)?)?",
    r"Opus\s+\d+(?:\.\d+)?",
    r"Gemini\s+CLI",
    r"Gemini(?:\s+\d+(?:\.\d+)?)?(?:\s+Flash)?",
    r"Qwen[0-9A-Za-z.\-+]*",
    r"DeepSeek(?:[- ][A-Za-z0-9.]+)?",
    r"Kimi(?:\s+K?\d+(?:\.\d+)?)?",
    r"Moonshot",
    r"GLM[- ]?\d+(?:\.\d+)?",
    r"MiMo[-A-Za-z0-9.]+",
    r"Codex",
    r"Agents?\s+SDK",
    r"MCP",
    r"Chrome",
    r"Shopify",
    r"LiteParse",
    r"PDF",
    r"OpenAI",
    r"Anthropic",
    r"Google",
    r"腾讯云",
    r"百度千帆",
    r"通义灵码",
    r"飞书",
    r"钉钉",
]

_CONNECTOR_WORDS = {
    "and",
    "with",
    "for",
    "from",
    "into",
    "about",
    "the",
    "a",
    "an",
    "new",
    "now",
    "update",
    "updates",
    "launch",
    "launches",
    "introducing",
}


def build_editorial_item(
    item: FeedItem,
    *,
    language: str = "zh-CN",
    max_headline_chars: int = 32,
    max_body_chars: int = 180,
    prevent_verbatim_chars: int = 30,
) -> EditorialItem:
    title_text = _clean_raw_text(item.title)
    summary_text = _clean_raw_text(item.summary)
    raw_text = _clean_raw_text(f"{title_text}。{summary_text}")
    entities = _extract_entities(title_text) or _extract_entities(raw_text)
    topic = _infer_topic(raw_text, item)

    if language != "zh-CN":
        headline = _clip_text(item.title.strip(), max_headline_chars)
        body = _clip_text(_clean_raw_text(item.summary) or item.title.strip(), max_body_chars)
        return EditorialItem(headline=headline, body=body, quick_title=headline)

    headline = _make_headline(title_text or raw_text, entities, topic, max_headline_chars)
    body = _make_body(raw_text, entities, topic, max_body_chars)
    body = _avoid_verbatim(body, raw_text, prevent_verbatim_chars, entities, topic, max_body_chars)

    return EditorialItem(
        headline=headline,
        body=body,
        quick_title=headline,
    )


def _clean_raw_text(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"\bRT\s+@?[A-Za-z0-9_]+:?", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"@[A-Za-z0-9_]+", " ", value)
    value = re.sub(r"#[^#\s]+#", " ", value)
    value = re.sub(r"\[[^\]]{1,20}\]", " ", value)
    value = re.sub(r"^\s*\d+[.)、]\s*", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n-—:：,，。")


def _extract_entities(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for pattern in _PRODUCT_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = _normalize_entity(match.group(0))
            if value:
                found.append((match.start(), value))

    # Pick up important versioned names that are not covered above.
    for match in re.finditer(r"\b[A-Z][A-Za-z]+[- ]?\d+(?:\.\d+)?(?:[-A-Za-z0-9.]*)\b", text):
        value = _normalize_entity(match.group(0))
        if value and value.lower() not in _CONNECTOR_WORDS:
            found.append((match.start(), value))

    found.sort(key=lambda row: row[0])

    entities: list[str] = []
    seen: set[str] = set()
    for _, entity in found:
        key = entity.lower().replace(" ", "")
        if key in seen:
            continue
        if any(
            key != old and (key.startswith(old) or old.startswith(key) or key in old or old in key)
            for old in seen
        ):
            continue
        # Avoid keeping the generic vendor if a specific product already exists.
        if entity in {"OpenAI", "Anthropic", "Google"} and any(
            existing.lower().startswith(("gpt", "claude", "gemini", "codex"))
            for existing in entities
        ):
            continue
        entities.append(entity)
        seen.add(key)
        if len(entities) >= 5:
            break
    return entities


def _normalize_entity(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip(" ,，。:：")
    if re.fullmatch(r"AI[- ]?\d+", text, flags=re.IGNORECASE):
        return ""
    replacements = {
        "gpt": "GPT",
        "claude": "Claude",
        "codex": "Codex",
        "gemini": "Gemini",
        "qwen": "Qwen",
        "deepseek": "DeepSeek",
        "kimi": "Kimi",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google": "Google",
        "mcp": "MCP",
        "pdf": "PDF",
    }
    for lower, canonical in replacements.items():
        if text.lower() == lower:
            return canonical
    text = re.sub(r"^gpt", "GPT", text, flags=re.IGNORECASE)
    text = re.sub(r"^qwen", "Qwen", text, flags=re.IGNORECASE)
    text = re.sub(r"^deepseek", "DeepSeek", text, flags=re.IGNORECASE)
    return text


def _infer_topic(text: str, item: FeedItem) -> str:
    lower = text.lower()
    if any(word in lower for word in ("codex", "agent", "agents sdk", "cli", "mcp", "browser control")):
        return "execution"
    if any(word in lower for word in ("chrome", "browser", "workspace", "app", "shopify")):
        return "entry"
    if any(word in lower for word in ("gpt", "claude", "gemini", "opus", "model", "模型", "sota")):
        return "model"
    if any(word in lower for word in ("qwen", "deepseek", "kimi", "千帆", "腾讯", "通义", "百度")):
        return "china"
    if item.source_region.lower() == "cn":
        return "china"
    return "general"


def _make_headline(text: str, entities: list[str], topic: str, max_chars: int) -> str:
    lower = text.lower()
    primary = entities[0] if entities else ""

    if "gpt" in lower and "codex" in lower:
        headline = "GPT 与 Codex 推高编码 Agent 竞争"
    elif "qwen" in lower and any(word in lower for word in ("claude", "opus", "coding", "code")):
        headline = "Qwen 继续冲击闭源模型叙事"
    elif "gemini cli" in lower or ("gemini" in lower and "cli" in lower):
        headline = "Gemini CLI 加速进入开发工作流"
    elif "agents sdk" in lower:
        headline = "Agents SDK 补强工具编排能力"
    elif "chrome" in lower:
        headline = "Chrome 把 AI 入口前移到浏览器"
    elif "shopify" in lower:
        headline = "Shopify 把 AI 写进组织流程"
    elif "liteparse" in lower or "pdf" in lower:
        headline = "PDF 工具链继续被 AI 重做"
    elif "kimi" in lower or "moonshot" in lower:
        headline = "Kimi 更新继续推动国产模型竞争"
    elif "claude" in lower or "opus" in lower:
        headline = "Claude 新模型继续卷向复杂任务"
    elif "qwen" in lower:
        headline = "Qwen 开源模型继续补齐能力短板"
    elif primary:
        suffix = {
            "execution": "进入 Agent 执行层竞争",
            "entry": "争夺 AI 高频入口",
            "china": "释放国内产品化信号",
            "model": "刷新模型能力讨论",
            "general": "成为本周 AI 讨论焦点",
        }[topic]
        headline = f"{primary} {suffix}"
    else:
        headline = "AI 产品继续向真实工作流靠近"

    return _clip_text(headline, max_chars)


def _make_body(text: str, entities: list[str], topic: str, max_chars: int) -> str:
    subject = _subject_text(entities)
    signal = _signal_sentence(text)

    if topic == "execution":
        body = (
            f"这一周值得关注的是 {subject}：讨论重点已经不只是模型能不能回答，"
            "而是能不能把代码、浏览器、工具和权限串成稳定的执行闭环。"
            "这会直接改变开发者把 AI 放进日常工作流的方式。"
        )
    elif topic == "model":
        body = (
            f"这一周值得关注的是 {subject}：模型竞争重新回到编码、长上下文和复杂任务交付上。"
            "真正的变化不是单次跑分，而是这些能力正在影响团队对开发、内容和企业流程的模型选择。"
        )
    elif topic == "entry":
        body = (
            f"这一周值得关注的是 {subject}：AI 正在从独立应用进入浏览器、桌面和业务系统入口。"
            "入口变化通常比单个功能更关键，因为它会改变用户调用 AI 的默认路径和频率。"
        )
    elif topic == "china":
        body = (
            f"这一周值得关注的是 {subject}：国内厂商继续把模型能力包装成可调用、可部署的产品接口。"
            "相比单次宣传，这类更新更能说明 AI 正在进入真实业务流程。"
        )
    else:
        body = (
            f"这一周值得关注的是 {subject}：它不是孤立新闻，而是 AI 产品从展示能力转向稳定交付的一部分。"
            "短期看是工具更新，长期看会影响团队怎么选择模型和工作流。"
        )

    if signal and len(body) + len(signal) <= max_chars:
        body = f"{body}{signal}"

    return _clip_sentence(body, max_chars)


def _subject_text(entities: list[str]) -> str:
    if not entities:
        return "这组 AI 产品动态"
    if len(entities) == 1:
        return entities[0]
    if len(entities) == 2:
        return f"{entities[0]} 与 {entities[1]}"
    return "、".join(entities[:3])


def _signal_sentence(text: str) -> str:
    lower = text.lower()
    if "shopify" in lower:
        return "这类变化说明 AI 已经从工具采购，进入组织流程和管理规则。"
    if any(word in lower for word in ("price", "token", "上下文", "context", "1m", "400k")):
        return "同时，价格、上下文窗口和额度也开始成为用户选择模型时绕不开的变量。"
    if any(word in lower for word in ("ui", "design", "image", "前端", "视觉", "画图")):
        return "图像生成和前端实现的组合，也让“先画再做”的开发流程更现实。"
    if any(word in lower for word in ("open source", "开源", "dense", "a3b")):
        return "开源模型的节奏仍在追近闭源模型，开发者可替换的选择变多了。"
    if any(word in lower for word in ("system prompt", "token counter", "token count", "提示词")):
        return "围绕系统提示词和 Token 管理的讨论升温，说明模型产品开始进入精细化运营阶段。"
    return ""


def _avoid_verbatim(
    body: str,
    raw_text: str,
    prevent_chars: int,
    entities: list[str],
    topic: str,
    max_chars: int,
) -> str:
    if prevent_chars <= 0:
        return body

    compact_raw = re.sub(r"\s+", "", raw_text)
    compact_body = re.sub(r"\s+", "", body)
    if not compact_raw or not compact_body:
        return body

    for idx in range(0, max(len(compact_body) - prevent_chars + 1, 0)):
        chunk = compact_body[idx : idx + prevent_chars]
        if chunk and chunk in compact_raw:
            fallback = _fallback_body(entities, topic)
            return _clip_sentence(fallback, max_chars)
    return body


def _fallback_body(entities: list[str], topic: str) -> str:
    subject = _subject_text(entities)
    mapping = {
        "execution": f"{subject} 的重点在于把模型能力接进真实执行链路。相比单点功能，接下来更值得看的是工具调用、权限控制和交付稳定性。",
        "model": f"{subject} 让模型竞争继续回到复杂任务能力。对用户来说，差异会体现在编码、长文档和多步骤工作能不能稳定完成。",
        "entry": f"{subject} 说明 AI 正在抢占更高频的用户入口。入口一旦前移，产品分发和用户习惯都会跟着变化。",
        "china": f"{subject} 释放的是国内 AI 产品化信号。真正值得看的是这些能力能否变成企业可接入、可审计、可持续使用的接口。",
        "general": f"{subject} 体现了 AI 产品从能力展示走向工作流落地。后续观察重点是它能不能带来稳定、可复用的效率提升。",
    }
    return mapping[topic]


def _clip_text(text: str, max_chars: int) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max(max_chars - 1, 1)].rstrip(" ，,。:：") + "…"


def _clip_sentence(text: str, max_chars: int) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    value = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", value)
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    clipped = value[:max_chars].rstrip("，,；;：:")
    last_stop = max(clipped.rfind("。"), clipped.rfind("！"), clipped.rfind("？"))
    if last_stop >= max_chars * 0.55:
        return clipped[: last_stop + 1]
    return clipped.rstrip("，,；;：:") + "。"
