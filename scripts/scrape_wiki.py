"""Scrape a Fandom/wiki page for a manhwa.

Tries several common Fandom URL shapes, then extracts paragraphs and bullets
from the main content area. Volumes are capped via CLI args (defaults align
with `config.json` limits).

Usage:
    python -m scripts.scrape_wiki --title "Solo Leveling" --output storage/temp/wiki.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("wiki")

USER_AGENT = "manhwa-mvp/0.1 (+https://example.local)"
HEADERS = {"User-Agent": USER_AGENT}

# Slug variants commonly found on Fandom wikis.
def candidate_urls(title: str) -> list[str]:
    slug_underscore = re.sub(r"\s+", "_", title.strip())
    slug_dash = re.sub(r"\s+", "-", title.strip().lower())
    slug_compact = re.sub(r"\s+", "", title.strip().lower())
    return [
        f"https://{slug_compact}.fandom.com/wiki/{slug_underscore}",
        f"https://manhwa.fandom.com/wiki/{slug_underscore}",
        f"https://manga.fandom.com/wiki/{slug_underscore}",
        f"https://{slug_dash}.fandom.com/wiki/Main_Page",
        f"https://en.wikipedia.org/wiki/{slug_underscore}",
    ]


def fetch_html(url: str, timeout: int = 15) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 500:
            return r.text
        logger.debug("URL %s returned status=%s len=%s", url, r.status_code, len(r.text))
    except requests.RequestException as exc:
        logger.debug("Request failed for %s: %s", url, exc)
    return None


def extract_text_blocks(
    html: str, max_paragraphs: int, max_bullets: int
) -> tuple[list[str], list[str]]:
    """Pull <p> and <li> textual content from the main content area."""
    soup = BeautifulSoup(html, "lxml")

    # Fandom & Wikipedia both expose a recognizable main content container.
    main = (
        soup.find("div", class_="mw-parser-output")
        or soup.find("div", id="content")
        or soup
    )

    paragraphs: list[str] = []
    for p in main.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) >= 80:  # skip tiny / empty paragraphs
            paragraphs.append(text)
        if len(paragraphs) >= max_paragraphs:
            break

    bullets: list[str] = []
    for li in main.find_all("li"):
        text = li.get_text(" ", strip=True)
        if 20 <= len(text) <= 400:
            bullets.append(text)
        if len(bullets) >= max_bullets:
            break

    return paragraphs, bullets


def scrape(
    title: str, max_paragraphs: int, max_bullets: int
) -> dict[str, object]:
    for url in candidate_urls(title):
        logger.info("Trying %s", url)
        html = fetch_html(url)
        if not html:
            continue
        paragraphs, bullets = extract_text_blocks(html, max_paragraphs, max_bullets)
        if not paragraphs and not bullets:
            continue
        events = _to_events(paragraphs, bullets)
        return {"title": title, "source_url": url, "events": events}

    logger.warning("No usable wiki page found for %s", title)
    return {"title": title, "source_url": None, "events": []}


def _to_events(paragraphs: Iterable[str], bullets: Iterable[str]) -> list[str]:
    """Merge paragraphs + bullets into a single ordered 'events' list."""
    events: list[str] = []
    for p in paragraphs:
        events.append(p)
    for b in bullets:
        events.append(f"- {b}")
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape wiki/fandom for a manhwa.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-paragraphs", type=int, default=10)
    parser.add_argument("--max-bullets", type=int, default=20)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    data = scrape(args.title, args.max_paragraphs, args.max_bullets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote %s (%d events)", args.output, len(data["events"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
