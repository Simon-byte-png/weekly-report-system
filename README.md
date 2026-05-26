# Weekly Report System

一个面向 AI 信息跟踪的周报生成 CLI / Skill 原型。它把 RSS、Atom、sitemap 和手工事实源聚合到统一事件模型中，再通过规则筛选、去重、排序和模板渲染，生成可直接复制发布的 Markdown 周报。

## Features

- 支持 `rss_atom`、`sitemap`、`manual_source` 三类信息源。
- 支持创作者源、官方源、趋势源的分层配置。
- 内置时间窗口过滤、事件聚类去重、主题/域名限额。
- 支持来源健康度降权，降低低可用源对主条选择的影响。
- 输出固定结构 Markdown：标题、编号主条、快讯、外显链接。
- 可通过 GitHub Actions 定时运行，也可本地手动生成。

## Usage

```bash
./weekly-report run \
  --end-date 2026-04-23 \
  --window-days 7 \
  --tz Asia/Shanghai \
  --config config/weekly-report.json \
  --output outputs/weekly-report-2026-04-23.md
```

## Configuration

Main configuration lives in `config/weekly-report.json`:

- `sources.creators[]`: creator and analyst feeds
- `sources.official[]`: official sources for fact anchors
- `sources.trend[]`: trend and quick-news candidates
- `manual_items[]`: manually curated facts
- `rules.*`: selection, deduplication, source-health and balance rules
- `render.*`: output language, style, title and display options

## GitHub Actions

The workflow in `.github/workflows/weekly-report.yml` can run on a schedule or manually through `workflow_dispatch`. Generated reports are uploaded as workflow artifacts instead of being committed to the repository.

## Tests

```bash
python3 -m unittest tests/test_weekly_report.py
```

## Notes

This project currently uses only Python standard library modules. Historical generated reports are intentionally excluded from the public repository through `.gitignore`.

## License

MIT
