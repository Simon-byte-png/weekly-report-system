from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
from pathlib import Path

from zoneinfo import ZoneInfo

from .render import render_report, select_items
from .sources import (
    fetch_source_items_with_status,
    iter_window_items,
    load_manual_items,
    load_sources,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weekly-report")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Generate weekly report markdown")
    run.add_argument("--end-date", required=True, help="Window end date (YYYY-MM-DD)")
    run.add_argument("--window-days", type=int, default=7, help="Rolling window days")
    run.add_argument("--tz", default="Asia/Shanghai", help="Timezone name")
    run.add_argument(
        "--config",
        default="config/weekly-report.json",
        help="Path to config JSON",
    )
    run.add_argument("--output", required=True, help="Output markdown file path")

    return parser


def run_command(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.tz)
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    now = datetime.now(tz)

    # For end_date=2026-04-20 and window_days=7, this yields 2026-04-13.
    start_date = end_date - timedelta(days=max(args.window_days, 0))
    start_dt = datetime.combine(start_date, time(0, 0, 0), tz)
    end_dt = datetime.combine(end_date, time(23, 59, 59), tz)

    config_path = Path(args.config)
    sources, config = load_sources(config_path)

    all_items = []
    errors = []
    statuses = []
    for source in sources:
        items, status = fetch_source_items_with_status(source, tz, now)
        for item in items:
            item.source_health = status.health
        all_items.extend(items)
        statuses.append(status)
        if not status.success:
            errors.append((source.name, status.error))

    all_items.extend(load_manual_items(config, tz))
    window_items = iter_window_items(all_items, start_dt, end_dt)

    rules = config.get("rules", {})
    main_limit = int(rules.get("main_items", 6))
    quick_limit = int(rules.get("quick_items", 5))
    require_official_for_main = bool(rules.get("require_official_for_main", False))
    main_from_creators_min_ratio = float(rules.get("main_from_creators_min_ratio", 0.6))
    max_items_per_topic = int(rules.get("max_items_per_topic", 1))
    max_items_per_domain = int(rules.get("max_items_per_domain", 2))
    source_health_threshold = float(rules.get("source_health_threshold", 0.35))
    require_region_balance = bool(rules.get("require_region_balance", True))

    has_creators = any(source.source_type == "creator" for source in sources)
    if not has_creators:
        print("Warning: config has no sources.creators[]; falling back to legacy source pool.")

    main_items, quick_items = select_items(
        window_items,
        main_limit=main_limit,
        quick_limit=quick_limit,
        require_official_for_main=require_official_for_main,
        main_from_creators_min_ratio=main_from_creators_min_ratio,
        max_items_per_topic=max_items_per_topic,
        max_items_per_domain=max_items_per_domain,
        source_health_threshold=source_health_threshold,
        require_region_balance=require_region_balance,
    )

    render_cfg = config.get("render", {})
    title_prefix = render_cfg.get("title_prefix", "AI产品研习社周报")
    render_language = render_cfg.get("language", "zh-CN")
    show_source = bool(render_cfg.get("show_source", False))
    editorial_mode = str(render_cfg.get("editorial_mode", "viewpoint"))
    max_headline_chars = int(render_cfg.get("max_headline_chars", 32))
    max_body_chars = int(render_cfg.get("max_body_chars", 180))
    prevent_verbatim_chars = int(render_cfg.get("prevent_verbatim_chars", 30))

    markdown = render_report(
        end_date=end_dt,
        main_items=main_items,
        quick_items=quick_items,
        title_prefix=title_prefix,
        language=render_language,
        show_source=show_source,
        editorial_mode=editorial_mode,
        max_headline_chars=max_headline_chars,
        max_body_chars=max_body_chars,
        prevent_verbatim_chars=prevent_verbatim_chars,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    print(f"Report generated: {output_path}")
    print(f"Time window: {start_dt.isoformat()} ~ {end_dt.isoformat()}")
    print(f"Fetched items: {len(all_items)} | In window: {len(window_items)}")
    print(f"Main items: {len(main_items)} | Quick items: {len(quick_items)}")
    if main_items:
        creator_count = sum(1 for x in main_items if x.source_type == "creator")
        print(f"Main creator ratio: {creator_count / len(main_items):.2f}")
        region_coverage = sorted({x.source_region.lower() for x in main_items})
        print(f"Main region coverage: {','.join(region_coverage)}")

    if statuses:
        low_health = [s for s in statuses if s.health < source_health_threshold]
        print(f"Sources fetched: {len(statuses)} | low-health sources: {len(low_health)}")

    if errors:
        print("Fetch warnings:")
        for name, message in errors:
            print(f"- {name}: {message}")

    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        return run_command(args)

    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
