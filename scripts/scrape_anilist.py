"""Fetch a manhwa entry from the AniList GraphQL API.

Usage:
    python -m scripts.scrape_anilist --title "Solo Leveling" --output storage/temp/anilist.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import requests

ANILIST_URL = "https://graphql.anilist.co"

QUERY = """
query ($search: String) {
  Media(search: $search, type: MANGA) {
    id
    title { romaji english native }
    description(asHtml: false)
    genres
    status
    startDate { year }
    averageScore
    tags(sort: RANK) { name rank }
    staff(perPage: 5) { nodes { name { full } } }
    studios { nodes { name } }
  }
}
"""

logger = logging.getLogger("anilist")


def fetch_anilist(title: str, timeout: int = 20) -> dict[str, Any]:
    """Call AniList GraphQL API and return the parsed JSON response."""
    logger.info("Querying AniList for title=%r", title)
    response = requests.post(
        ANILIST_URL,
        json={"query": QUERY, "variables": {"search": title}},
        timeout=timeout,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"AniList returned errors: {payload['errors']}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape AniList for a manhwa entry.")
    parser.add_argument("--title", required=True, help="Manhwa title to search.")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the JSON.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    try:
        data = fetch_anilist(args.title)
    except Exception as exc:  # noqa: BLE001 - top-level CLI handler
        logger.error("AniList fetch failed: %s", exc)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
