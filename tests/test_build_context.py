"""Smoke tests for scripts.build_context.

Run with:
    pytest -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_context import build_context, NARRATIVE_RULES  # noqa: E402


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_context_full(tmp_path: Path) -> None:
    write(
        tmp_path / "anilist.json",
        {
            "data": {
                "Media": {
                    "title": {"romaji": "Solo Leveling", "english": "Solo Leveling"},
                    "description": "An ordinary hunter awakens a unique power.",
                    "genres": ["Action", "Fantasy"],
                    "status": "FINISHED",
                    "tags": [{"name": "Cultivation"}, {"name": "Dungeon"}],
                }
            }
        },
    )
    write(
        tmp_path / "wiki.json",
        {
            "title": "Solo Leveling",
            "source_url": "https://example.local",
            "events": ["Sung Jin-Woo enters the double dungeon.", "He awakens a hidden system."],
        },
    )
    write(
        tmp_path / "community.json",
        {"community_angles": ["Power fantasy peak", "Best art in modern manhwa"]},
    )
    write(
        tmp_path / "ocr.json",
        {
            "ocr_text_sample": ["AAARGH!", "Run!", "What is this place?"],
            "emotional_cues": ["AAARGH!", "Run!"],
        },
    )

    ctx = build_context(tmp_path)

    assert ctx["title"]["romaji"] == "Solo Leveling"
    assert "Action" in ctx["genres"]
    assert ctx["status"] == "FINISHED"
    assert ctx["narrative_primary"]["wiki_events"]
    assert ctx["narrative_primary"]["community_angles"]
    assert ctx["ocr_support"]["emotional_cues"] == ["AAARGH!", "Run!"]
    assert ctx["rules"] == NARRATIVE_RULES


def test_build_context_missing_files(tmp_path: Path) -> None:
    """Should not crash even if every source is missing."""
    ctx = build_context(tmp_path)
    assert ctx["genres"] == []
    assert ctx["narrative_primary"]["wiki_events"] == []
    assert ctx["narrative_primary"]["community_angles"] == []
    assert ctx["ocr_support"]["emotional_cues"] == []
    assert ctx["rules"] == NARRATIVE_RULES


def test_build_context_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "anilist.json").write_text("not-json", encoding="utf-8")
    ctx = build_context(tmp_path)
    assert ctx["genres"] == []
