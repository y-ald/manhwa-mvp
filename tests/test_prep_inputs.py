"""Smoke tests for scripts.prep_inputs.

Run with:
    pytest -q tests/test_prep_inputs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# These tests need PIL + (sometimes) PyMuPDF; skip cleanly if not installed.
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from scripts.prep_inputs import (  # noqa: E402
    list_inputs,
    copy_images,
    normalize_width,
    stack_vertical,
    slice_vertical,
)


def make_solid_image(path: Path, w: int, h: int, color=(200, 100, 50)) -> None:
    img = Image.new("RGB", (w, h), color)
    img.save(path, "PNG")


def test_list_inputs_separates_images_and_pdfs(tmp_path: Path) -> None:
    make_solid_image(tmp_path / "001.png", 800, 1200)
    make_solid_image(tmp_path / "002.jpg", 800, 1200)
    (tmp_path / "fake.pdf").write_bytes(b"%PDF-1.4\n%dummy")
    (tmp_path / "ignore.txt").write_text("nope")

    images, pdfs = list_inputs(tmp_path)

    assert len(images) == 2
    assert len(pdfs) == 1
    assert all(p.suffix.lower() in {".png", ".jpg"} for p in images)
    assert pdfs[0].name == "fake.pdf"


def test_copy_images_skips_existing(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    make_solid_image(src_dir / "a.png", 100, 100)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    n1 = copy_images([src_dir / "a.png"], out_dir)
    assert n1 == 1
    assert (out_dir / "a.png").exists()

    n2 = copy_images([src_dir / "a.png"], out_dir)
    assert n2 == 0  # cache hit


def test_normalize_width_preserves_ratio() -> None:
    img = Image.new("RGB", (640, 480))
    out = normalize_width(img, 1280)
    assert out.width == 1280
    assert out.height == 960  # 480 * 2


def test_stack_vertical_glues_images() -> None:
    a = Image.new("RGB", (1280, 200), (255, 0, 0))
    b = Image.new("RGB", (1280, 300), (0, 255, 0))
    stacks = stack_vertical([a, b], target_width=1280, max_height=10000)
    assert len(stacks) == 1
    assert stacks[0].size == (1280, 500)


def test_stack_vertical_splits_above_max_height() -> None:
    imgs = [Image.new("RGB", (1280, 400)) for _ in range(5)]
    stacks = stack_vertical(imgs, target_width=1280, max_height=900)
    # 400+400=800 OK, +400 would be 1200 > 900 -> split
    assert len(stacks) >= 2
    assert all(s.height <= 900 for s in stacks)


def test_slice_vertical_pads_last_slice() -> None:
    stack = Image.new("RGB", (1280, 1500))
    slices = slice_vertical(stack, slice_height=720, target_width=1280)
    # 1500 / 720 -> 3 slices (last one padded)
    assert len(slices) == 3
    assert all(s.size == (1280, 720) for s in slices)


def test_pdf_extraction_end_to_end(tmp_path: Path) -> None:
    """Write a 2-page PDF with PyMuPDF then run process_pdf on it."""
    fitz = pytest.importorskip("fitz")
    from scripts.prep_inputs import process_pdf

    # Build a tiny 2-page PDF with a colored rectangle on each page.
    pdf_path = tmp_path / "demo.pdf"
    doc = fitz.open()
    for color in [(1, 0, 0), (0, 0.6, 0)]:
        page = doc.new_page(width=400, height=600)
        page.draw_rect(fitz.Rect(20, 20, 380, 580), color=color, fill=color)
    doc.save(str(pdf_path))
    doc.close()

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    written = process_pdf(
        pdf_path,
        out_dir,
        target_width=1280,
        slice_height=720,
        dpi=100,
        use_raw=False,
        max_merged_height=50000,
        max_pages=10,
    )
    assert written >= 1
    slices = sorted(out_dir.glob("demo__slice_*.png"))
    assert slices
