"""Unit tests for the title -> folder slug helper used by the orchestrator."""
from __future__ import annotations

import pytest

from run_pipeline import slugify


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Solo Leveling", "solo_leveling"),
        ("solo_leveling", "solo_leveling"),
        ("  Solo   Leveling  ", "solo_leveling"),
        ("Tower of God", "tower_of_god"),
        ("The 3rd Prince of the Fallen Kingdom Returns",
         "the_3rd_prince_of_the_fallen_kingdom_returns"),
        ("Tower of God: Récits", "tower_of_god_recits"),
        ("Noblesse - Awakening!", "noblesse_awakening"),
        ("나 혼자만 레벨업", "untitled"),  # pure non-ascii falls back to placeholder
        ("", "untitled"),
        ("---", "untitled"),
    ],
)
def test_slugify(title: str, expected: str) -> None:
    assert slugify(title) == expected


def test_slugify_is_idempotent() -> None:
    sample = "The 3rd Prince of the Fallen Kingdom Returns"
    once = slugify(sample)
    twice = slugify(once)
    assert once == twice
