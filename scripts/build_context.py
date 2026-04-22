"""Merge per-source JSON files into a single context.json used by Gemini.

Loads (all optional, missing files are tolerated):
  - anilist.json
  - wiki.json
  - community.json
  - ocr.json

Usage:
    python -m scripts.build_context --temp-dir storage/temp \
        --output storage/temp/context.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("context")

NARRATIVE_RULES = [
    "AniList + wiki + community = primary narrative sources.",
    "OCR is emotional reinforcement only, never the storyline.",
    "Never copy any exact dialogue.",
    "Never follow chapter / page order.",
    "Output must be French, immersive, oral storytelling tone.",
    "No academic summary, no bullet lists, no chapter references.",
    "Audio is the main content; images are a secondary visual support.",
]


def safe_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.warning("Missing file %s, treating as empty.", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in %s: %s", path, exc)
        return {}


def build_context(temp_dir: Path) -> dict[str, Any]:
    anilist = safe_load(temp_dir / "anilist.json")
    wiki = safe_load(temp_dir / "wiki.json")
    community = safe_load(temp_dir / "community.json")
    ocr = safe_load(temp_dir / "ocr.json")

    media = (anilist.get("data") or {}).get("Media") or {}

    return {
        "title": media.get("title") or {"romaji": wiki.get("title")},
        "description": media.get("description"),
        "genres": media.get("genres", []),
        "status": media.get("status"),
        "tags": [t.get("name") for t in (media.get("tags") or []) if t.get("name")][:10],
        "narrative_primary": {
            "wiki_events": wiki.get("events", []),
            "community_angles": community.get("community_angles", []),
        },
        "ocr_support": {
            "emotional_cues": ocr.get("emotional_cues", []),
        },
        "rules": NARRATIVE_RULES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge source JSONs into context.json.")
    parser.add_argument("--temp-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    context = build_context(args.temp_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
