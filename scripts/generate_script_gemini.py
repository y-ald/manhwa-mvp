"""Generate the French narration script via Google Gemini.

Uses the official `google-genai` SDK (the newer one, not the deprecated
`google-generativeai`).

Env:
    GEMINI_API_KEY

Usage:
    python -m scripts.generate_script_gemini \
        --context storage/temp/context.json \
        --output storage/temp/script.txt \
        --model gemini-2.5-pro
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("gemini")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompts() -> tuple[str, str]:
    system = (PROMPTS_DIR / "narrative_system.txt").read_text(encoding="utf-8")
    user_tpl = (PROMPTS_DIR / "narrative_user.txt").read_text(encoding="utf-8")
    return system, user_tpl


def call_gemini(system_prompt: str, user_prompt: str, model: str) -> str:
    """Call Gemini with a system + user prompt and return the text."""
    # Lazy import keeps tests / lint fast when SDK is missing.
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in environment.")

    client = genai.Client(api_key=api_key)

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.8,
        max_output_tokens=4096,
    )

    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=config,
    )

    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def generate(context_path: Path, model: str) -> str:
    context = json.loads(context_path.read_text(encoding="utf-8"))
    system, user_tpl = load_prompts()
    user = user_tpl.replace(
        "{context_json}",
        json.dumps(context, ensure_ascii=False, indent=2),
    )
    logger.info("Calling Gemini model=%s", model)
    return call_gemini(system, user, model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate narration via Gemini.")
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="gemini-2.5-pro")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")
    load_dotenv()

    try:
        script = generate(args.context, args.model)
    except Exception as exc:  # noqa: BLE001
        logger.error("Gemini generation failed: %s", exc)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(script + "\n", encoding="utf-8")
    logger.info("Wrote %s (%d chars)", args.output, len(script))
    return 0


if __name__ == "__main__":
    sys.exit(main())
