#!/usr/bin/env python3
"""Export the last 24 hours of SMZDM deals from the existing SQLite database.

This deliberately reads the crawler's existing schema without changing the
database.  The output contains only the fields Hermes needs for a concise
shopping summary, and no volatile export timestamp so an unchanged candidate
set does not create an unnecessary Git commit.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Asia/Shanghai")
WINDOW_HOURS = 24
REQUIRED_COLUMNS = (
    "article_id",
    "article_title",
    "article_price",
    "article_url",
    "article_time",
    "voted",
    "article_comment",
)


def parse_published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TIMEZONE)
    return parsed.astimezone(TIMEZONE)


def smzdm_article_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return None
    if hostname == "smzdm.com" or hostname.endswith(".smzdm.com"):
        return url
    return None


def count_value(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def read_deals(database: Path, now: datetime) -> list[dict[str, object]]:
    if not database.is_file() or database.stat().st_size == 0:
        raise RuntimeError(f"SQLite database does not exist or is empty: {database}")

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('ZDM')")
        }
        missing = sorted(set(REQUIRED_COLUMNS) - columns)
        if missing:
            raise RuntimeError(
                "ZDM table does not have the expected crawler columns: "
                + ", ".join(missing)
            )
        rows = connection.execute(
            """
            SELECT article_id, article_title, article_price, article_url,
                   article_time, voted, article_comment
            FROM ZDM
            """
        ).fetchall()

    window_start = now - timedelta(hours=WINDOW_HOURS)
    unique: dict[str, tuple[datetime, dict[str, object]]] = {}
    for article_id, title, price, url, article_time, voted, comments in rows:
        published = parse_published_at(article_time)
        canonical_url = smzdm_article_url(url)
        if published is None or not window_start <= published <= now:
            continue
        if canonical_url is None:
            continue

        normalized_id = str(article_id).strip() if article_id is not None else ""
        dedup_key = f"id:{normalized_id}" if normalized_id else f"url:{canonical_url}"
        deal = {
            "id": normalized_id or None,
            "title": str(title or "").strip(),
            "price": str(price or "").strip(),
            "url": canonical_url,
            "published_at": published.isoformat(),
            "votes": count_value(voted),
            "comments": count_value(comments),
        }
        previous = unique.get(dedup_key)
        if previous is None or published > previous[0]:
            unique[dedup_key] = (published, deal)

    return [
        item[1]
        for item in sorted(
            unique.values(), key=lambda item: (item[0], str(item[1]["id"])), reverse=True
        )
    ]


def export(database: Path, output: Path) -> int:
    now = datetime.now(TIMEZONE)
    deals = read_deals(database, now)
    payload = {
        "schema": "smzdm-recent-deals/v1",
        "timezone": "Asia/Shanghai",
        "window_hours": WINDOW_HOURS,
        "count": len(deals),
        "deals": deals,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "
", encoding="utf-8"
    )
    print(
        f"Exported {len(deals)} unique SMZDM deals published since "
        f"{(now - timedelta(hours=WINDOW_HOURS)).isoformat()}"
    )
    return len(deals)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="database_old.db", type=Path)
    parser.add_argument("--output", default="data/recent_deals.json", type=Path)
    args = parser.parse_args()
    try:
        export(args.database, args.output)
    except (OSError, RuntimeError, sqlite3.Error) as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
