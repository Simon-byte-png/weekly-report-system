from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from weekly_report.models import FeedItem
from weekly_report.render import render_report, select_items
from weekly_report.sources import iter_window_items, load_manual_items


class WeeklyReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = ZoneInfo("Asia/Shanghai")

    def test_window_filter_inclusive_boundaries(self) -> None:
        start = datetime(2026, 4, 13, 0, 0, 0, tzinfo=self.tz)
        end = datetime(2026, 4, 20, 23, 59, 59, tzinfo=self.tz)

        in_item = FeedItem(
            title="Inside Window",
            link="https://example.com/a",
            published_at=start,
            source_name="Official",
            source_url="https://example.com/feed",
            source_type="official",
        )
        out_item = FeedItem(
            title="Outside Window",
            link="https://example.com/b",
            published_at=datetime(2026, 4, 12, 23, 59, 59, tzinfo=self.tz),
            source_name="Official",
            source_url="https://example.com/feed",
            source_type="official",
        )

        filtered = iter_window_items([in_item, out_item], start, end)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].title, "Inside Window")

    def test_main_requires_official(self) -> None:
        official = FeedItem(
            title="Official AI Launch",
            link="https://example.com/official",
            published_at=datetime(2026, 4, 20, 9, 0, 0, tzinfo=self.tz),
            source_name="OpenAI",
            source_url="https://example.com/feed",
            source_type="official",
        )
        trend = FeedItem(
            title="AI Trend Recap",
            link="https://example.com/trend",
            published_at=datetime(2026, 4, 20, 10, 0, 0, tzinfo=self.tz),
            source_name="36Kr",
            source_url="https://example.com/feed",
            source_type="trend",
        )

        main, quick = select_items([trend, official], 1, 2, True)
        self.assertEqual(len(main), 1)
        self.assertEqual(main[0].source_type, "official")
        self.assertEqual(len(quick), 1)

    def test_manual_items_load_and_render(self) -> None:
        config = {
            "manual_items": [
                {
                    "title": "Claude Opus 4.7",
                    "date": "2026-04-16T09:00:00+08:00",
                    "link": "https://www.anthropic.com/news/claude-opus-4-7",
                    "source_name": "Anthropic",
                    "source_type": "official",
                }
            ]
        }

        manual = load_manual_items(config, self.tz)
        self.assertEqual(len(manual), 1)

        markdown = render_report(
            end_date=datetime(2026, 4, 20, 23, 59, 59, tzinfo=self.tz),
            main_items=manual,
            quick_items=[],
            title_prefix="AI产品研习社周报",
            language="zh-CN",
            show_source=False,
        )

        self.assertIn("Claude", markdown)
        self.assertIn("https://www.anthropic.com/news/claude-opus-4-7", markdown)
        self.assertNotIn("来源：", markdown)

    def test_creator_ratio_and_region_balance(self) -> None:
        items = [
            FeedItem(
                title="Codex for almost everything",
                link="https://creator-a.example.com/codex",
                published_at=datetime(2026, 4, 22, 10, 0, 0, tzinfo=self.tz),
                source_name="CreatorCN",
                source_url="https://creator-a.example.com/feed",
                source_type="creator",
                source_region="cn",
                source_priority=90,
            ),
            FeedItem(
                title="Codex for almost everything release breakdown",
                link="https://creator-b.example.com/codex-breakdown",
                published_at=datetime(2026, 4, 22, 11, 0, 0, tzinfo=self.tz),
                source_name="CreatorUS",
                source_url="https://creator-b.example.com/feed",
                source_type="creator",
                source_region="us",
                source_priority=88,
            ),
            FeedItem(
                title="Agents SDK update explained",
                link="https://creator-c.example.com/agents-sdk",
                published_at=datetime(2026, 4, 21, 8, 0, 0, tzinfo=self.tz),
                source_name="CreatorUS2",
                source_url="https://creator-c.example.com/feed",
                source_type="creator",
                source_region="us",
                source_priority=86,
            ),
            FeedItem(
                title="OpenAI official update",
                link="https://openai.com/index/agents",
                published_at=datetime(2026, 4, 21, 7, 0, 0, tzinfo=self.tz),
                source_name="OpenAI",
                source_url="https://openai.com/news/rss.xml",
                source_type="official",
                source_region="us",
                source_priority=40,
            ),
            FeedItem(
                title="Tencent cloud AI platform notes",
                link="https://cloud.tencent.com/doc/ai-platform",
                published_at=datetime(2026, 4, 21, 6, 0, 0, tzinfo=self.tz),
                source_name="Tencent",
                source_url="https://cloud.tencent.com/feed",
                source_type="official",
                source_region="cn",
                source_priority=45,
            ),
        ]

        main, quick = select_items(
            items,
            main_limit=4,
            quick_limit=4,
            require_official_for_main=False,
            main_from_creators_min_ratio=0.5,
            max_items_per_topic=1,
            max_items_per_domain=2,
            source_health_threshold=0.2,
            require_region_balance=True,
        )

        self.assertGreaterEqual(sum(1 for item in main if item.source_type == "creator"), 2)
        self.assertIn("cn", {item.source_region for item in main})
        self.assertIn("us", {item.source_region for item in main})
        # Topic dedupe: codex duplicates should collapse to a single main topic rep.
        codex_count = sum(1 for item in main if "codex" in item.title.lower())
        self.assertLessEqual(codex_count, 1)
        self.assertLessEqual(len(quick), 4)

    def test_render_chinese_quick_format_without_source(self) -> None:
        item = FeedItem(
            title="Codex for (almost) everything",
            link="https://openai.com/index/codex-for-almost-everything/",
            published_at=datetime(2026, 4, 22, 9, 0, 0, tzinfo=self.tz),
            source_name="OpenAI",
            source_url="https://openai.com/news/rss.xml",
            source_type="official",
        )
        markdown = render_report(
            end_date=datetime(2026, 4, 23, 23, 59, 59, tzinfo=self.tz),
            main_items=[item],
            quick_items=[item],
            title_prefix="AI产品研习社周报",
            language="zh-CN",
            show_source=False,
        )
        self.assertIn("Codex", markdown)
        self.assertIn("快讯", markdown)
        self.assertIn("（2026-04-22）", markdown)
        self.assertNotIn("OpenAI，2026-04-22", markdown)
        self.assertNotIn("来源：", markdown)
        self.assertNotIn("AI动态：", markdown)

    def test_render_extracts_viewpoint_from_long_creator_text(self) -> None:
        raw_title = (
            "GPT-5.5上线，OpenAI终于重铸荣光。 这几天，GPT可以说风头无两。 "
            "从GPT-image-2的疯狂破圈，直接让整个互联网变成了黑暗森林。 "
            "到了今天，GPT-5.5上线，再次重回了全球SOTA王座。 "
            "目前GPT-5.5在Codex中上下文还是只有400k，未来开放的API才有1M。 "
            "我在Codex上体验了一下，速度很快，给自己的几个产品上了一些新功能，几乎指哪打哪。"
        )
        item = FeedItem(
            title=raw_title,
            link="https://nitter.net/Khazix0918/status/2047457880346128752#m",
            published_at=datetime(2026, 4, 24, 9, 0, 0, tzinfo=self.tz),
            source_name="卡兹克",
            source_url="https://nitter.net/Khazix0918/rss",
            source_type="creator",
            source_region="cn",
        )

        markdown = render_report(
            end_date=datetime(2026, 4, 24, 23, 59, 59, tzinfo=self.tz),
            main_items=[item],
            quick_items=[],
            title_prefix="AI产品研习社周报",
            language="zh-CN",
            show_source=False,
            editorial_mode="viewpoint",
            max_headline_chars=32,
            max_body_chars=180,
            prevent_verbatim_chars=30,
        )

        self.assertIn("GPT 与 Codex 推高编码 Agent 竞争", markdown)
        self.assertIn("这一周值得关注的是", markdown)
        self.assertIn("链接：https://nitter.net/Khazix0918/status/2047457880346128752#m", markdown)
        self.assertNotIn("GPT-5.5上线，OpenAI终于重铸荣光", markdown)
        self.assertNotIn("几乎指哪打哪", markdown)

    def test_english_title_gets_natural_chinese_headline(self) -> None:
        item = FeedItem(
            title="Qwen3.6-27B: Flagship-Level Coding in a Dense Open Model",
            link="https://simonwillison.net/2026/Apr/22/qwen36-27b/",
            published_at=datetime(2026, 4, 23, 9, 0, 0, tzinfo=self.tz),
            source_name="Simon Willison",
            source_url="https://simonwillison.net/atom/everything/",
            source_type="creator",
            source_region="us",
        )

        markdown = render_report(
            end_date=datetime(2026, 4, 24, 23, 59, 59, tzinfo=self.tz),
            main_items=[item],
            quick_items=[item],
            title_prefix="AI产品研习社周报",
            language="zh-CN",
            show_source=False,
        )

        self.assertIn("Qwen 继续冲击闭源模型叙事", markdown)
        self.assertNotIn("AI动态：", markdown)
        self.assertNotIn("Flagship-Level、Coding、Dense", markdown)


if __name__ == "__main__":
    unittest.main()
