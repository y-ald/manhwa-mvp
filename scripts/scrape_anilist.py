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
    tags { name rank }
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
    if not response.ok:
        # AniList sends a JSON body with the actual error reason on 4xx.
        body_excerpt = response.text[:500] if response.text else "<empty>"
        raise RuntimeError(
            f"AniList HTTP {response.status_code}: {body_excerpt}"
        )
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"AniList returned errors: {payload['errors']}")
    return payload


def _empty_payload(reason: str) -> dict[str, Any]:
    """Placeholder payload written when AniList is unavailable or has no match.

    `build_context` already tolerates `Media: null`, so downstream steps can
    proceed using only wiki + community + OCR signals.
    """
    return {"data": {"Media": None}, "_error": reason}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape AniList for a manhwa entry.")
    parser.add_argument("--title", required=True, help="Manhwa title to search.")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on AniList failure instead of writing an empty placeholder.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    try:
        data = fetch_anilist(args.title)
        if not (data.get("data") or {}).get("Media"):
            logger.warning(
                "AniList: no entry for title=%r (will use wiki + community only).",
                args.title,
            )
            data = _empty_payload(f"no AniList match for {args.title!r}")
    except Exception as exc:  # noqa: BLE001 - top-level CLI handler
        if args.strict:
            logger.error("AniList fetch failed (strict mode): %s", exc)
            return 1
        logger.warning(
            "AniList unavailable (%s); writing empty placeholder, pipeline will "
            "continue with wiki + community only.", exc,
        )
        data = _empty_payload(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
