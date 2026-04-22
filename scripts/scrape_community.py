"""Pull short community angles about a manhwa from Reddit (PRAW).

Only short snippets (titles + a couple of top-comment lines) are kept.
Long copyrighted blocks are explicitly avoided.

Env vars (loaded from .env):
    REDDIT_CLIENT_ID
    REDDIT_CLIENT_SECRET
    REDDIT_USER_AGENT

Usage:
    python -m scripts.scrape_community --title "Solo Leveling" \
        --output storage/temp/community.json \
        --subreddits manhwa manga Sololeveling
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import praw
from dotenv import load_dotenv

logger = logging.getLogger("community")

MAX_SNIPPET_CHARS = 280  # keep snippets short to avoid copyright issues


def get_reddit_client() -> praw.Reddit:
    load_dotenv()
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "manhwa-mvp/0.1")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in environment."
        )
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_async=False,
    )


def trim(text: str, max_chars: int = MAX_SNIPPET_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def collect_angles(
    reddit: praw.Reddit,
    title: str,
    subreddits: list[str],
    max_posts_per_sub: int,
    max_comments_per_post: int,
    max_snippets: int,
) -> list[str]:
    angles: list[str] = []
    for sub_name in subreddits:
        try:
            sub = reddit.subreddit(sub_name)
            results = sub.search(title, limit=max_posts_per_sub, sort="relevance")
            for post in results:
                if post.title:
                    angles.append(trim(post.title))
                if max_comments_per_post > 0:
                    try:
                        post.comments.replace_more(limit=0)
                        for comment in post.comments[:max_comments_per_post]:
                            body = getattr(comment, "body", "") or ""
                            if 30 <= len(body) <= 600:
                                angles.append(trim(body))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("comment fetch failed: %s", exc)
                if len(angles) >= max_snippets:
                    return angles
        except Exception as exc:  # noqa: BLE001
            logger.warning("subreddit %s failed: %s", sub_name, exc)
            continue
    return angles[:max_snippets]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Reddit community angles.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--subreddits", nargs="+", default=["manhwa", "manga"])
    parser.add_argument("--max-posts-per-sub", type=int, default=8)
    parser.add_argument("--max-comments-per-post", type=int, default=5)
    parser.add_argument("--max-snippets", type=int, default=10)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    try:
        reddit = get_reddit_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Reddit init failed: %s", exc)
        # Soft-fail: write empty file so the pipeline can still proceed.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"community_angles": []}, indent=2), encoding="utf-8")
        return 1

    angles = collect_angles(
        reddit,
        args.title,
        args.subreddits,
        args.max_posts_per_sub,
        args.max_comments_per_post,
        args.max_snippets,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"community_angles": angles}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d angles)", args.output, len(angles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
