"""Prepare scans + PDFs into a flat directory of normalized PNG slices.

For each PDF, every page is extracted (PyMuPDF), normalized to a target width,
stacked vertically into one tall image, then re-cut into fixed-height slices.
Plain image files are simply copied (or symlinked) into the prepared folder.

Output:
    <scans_prepared>/
        <pdfname>__slice_001.png
        <pdfname>__slice_002.png
        ...
        <original_image_basename>.<ext>      (copied as-is)

Usage:
    python -m scripts.prep_inputs \
        --scans-dir storage/input/scans/solo_leveling \
        --output-dir storage/temp/scans_prepared \
        --target-width 1280 --slice-height 720 --dpi 150
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger("prep")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
PDF_EXTS = {".pdf"}


def list_inputs(scans_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return (image_paths, pdf_paths), sorted naturally."""
    from natsort import natsorted

    images: list[Path] = []
    pdfs: list[Path] = []
    if not scans_dir.exists():
        return images, pdfs
    for p in scans_dir.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in IMG_EXTS:
            images.append(p)
        elif ext in PDF_EXTS:
            pdfs.append(p)
    return natsorted(images), natsorted(pdfs)


def copy_images(images: list[Path], output_dir: Path) -> int:
    """Copy plain images into the prepared directory."""
    count = 0
    for src in images:
        dst = output_dir / src.name
        if dst.exists():
            continue  # cache
        try:
            shutil.copy2(src, dst)
            count += 1
        except OSError as exc:
            logger.warning("copy failed for %s: %s", src, exc)
    return count


def extract_pdf_pages(pdf_path: Path, dpi: int, use_raw: bool, max_pages: int):
    """Yield PIL.Image objects for each page of a PDF.

    Hybrid extraction: prefer the raw embedded image when there is exactly one
    image on a page (lossless, fast); fall back to a rasterization at given DPI.
    """
    import fitz
    from PIL import Image
    import io

    doc = fitz.open(str(pdf_path))
    try:
        n = min(len(doc), max_pages)
        for i in range(n):
            page = doc[i]
            img: "Image.Image | None" = None
            if use_raw:
                images_in_page = page.get_images(full=True)
                if len(images_in_page) == 1:
                    xref = images_in_page[0][0]
                    try:
                        base = doc.extract_image(xref)
                        img = Image.open(io.BytesIO(base["image"])).convert("RGB")
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("raw extract failed page %d: %s", i, exc)
                        img = None
            if img is None:
                # Fallback: rasterize the page at given DPI.
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            yield img
    finally:
        doc.close()


def normalize_width(img, target_width: int):
    """Resize an image to target_width while preserving aspect ratio."""
    from PIL import Image

    if img.width == target_width:
        return img
    ratio = target_width / img.width
    new_height = max(1, int(round(img.height * ratio)))
    resample = Image.Resampling.LANCZOS
    return img.resize((target_width, new_height), resample)


def stack_vertical(images: list, target_width: int, max_height: int) -> list:
    """Stack a list of PIL images vertically. Split into multiple stacks if the
    total height would exceed `max_height` (prevents giant decompression bombs).
    """
    from PIL import Image

    stacks = []
    current: list = []
    current_height = 0
    for img in images:
        if current_height + img.height > max_height and current:
            stacks.append(_glue(current, target_width, current_height, Image))
            current = []
            current_height = 0
        current.append(img)
        current_height += img.height
    if current:
        stacks.append(_glue(current, target_width, current_height, Image))
    return stacks


def _glue(images: list, width: int, total_height: int, Image) -> "Image.Image":
    canvas = Image.new("RGB", (width, total_height), (0, 0, 0))
    y = 0
    for img in images:
        canvas.paste(img, (0, y))
        y += img.height
    return canvas


def slice_vertical(stack, slice_height: int, target_width: int) -> list:
    """Cut a tall image into fixed-height slices, padding the last one."""
    from PIL import Image

    slices = []
    h = stack.height
    y = 0
    while y < h:
        bottom = min(y + slice_height, h)
        crop = stack.crop((0, y, target_width, bottom))
        if crop.height < slice_height:
            # pad bottom with black for visual continuity
            padded = Image.new("RGB", (target_width, slice_height), (0, 0, 0))
            padded.paste(crop, (0, 0))
            crop = padded
        slices.append(crop)
        y += slice_height
    return slices


def process_pdf(
    pdf_path: Path,
    output_dir: Path,
    target_width: int,
    slice_height: int,
    dpi: int,
    use_raw: bool,
    max_merged_height: int,
    max_pages: int,
) -> int:
    """Full PDF -> normalized slices pipeline. Returns slice count written."""
    stem = pdf_path.stem

    # Cache: skip if at least one slice already exists for this PDF.
    if any(output_dir.glob(f"{stem}__slice_*.png")):
        logger.info("PDF %s already processed (cache hit), skipping.", pdf_path.name)
        return 0

    logger.info("Extracting PDF pages: %s", pdf_path.name)
    pages = []
    for page_img in extract_pdf_pages(pdf_path, dpi, use_raw, max_pages):
        pages.append(normalize_width(page_img, target_width))
    if not pages:
        logger.warning("no pages extracted from %s", pdf_path.name)
        return 0

    logger.info("Stacking %d pages vertically...", len(pages))
    stacks = stack_vertical(pages, target_width, max_merged_height)

    logger.info("Slicing into %dpx tall chunks...", slice_height)
    written = 0
    slice_idx = 1
    for stack in stacks:
        for sl in slice_vertical(stack, slice_height, target_width):
            out = output_dir / f"{stem}__slice_{slice_idx:04d}.png"
            sl.save(out, "PNG", optimize=True)
            slice_idx += 1
            written += 1

    logger.info("Wrote %d slice(s) for %s", written, pdf_path.name)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prep scans + PDFs into normalized slices.")
    parser.add_argument("--scans-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--target-width", type=int, default=1280)
    parser.add_argument("--slice-height", type=int, default=720)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--max-merged-height", type=int, default=50000)
    parser.add_argument("--max-pages-per-pdf", type=int, default=200)
    parser.add_argument("--no-raw-extract", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    images, pdfs = list_inputs(args.scans_dir)
    if not images and not pdfs:
        logger.error("no images or PDFs found under %s", args.scans_dir)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    img_count = copy_images(images, args.output_dir)
    logger.info("Copied %d new image(s).", img_count)

    pdf_slice_count = 0
    for pdf in pdfs:
        try:
            pdf_slice_count += process_pdf(
                pdf,
                args.output_dir,
                target_width=args.target_width,
                slice_height=args.slice_height,
                dpi=args.dpi,
                use_raw=not args.no_raw_extract,
                max_merged_height=args.max_merged_height,
                max_pages=args.max_pages_per_pdf,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("PDF %s failed: %s", pdf.name, exc)
            continue

    # Success criterion: at least one usable file ready in the output dir,
    # whether it was produced now or already cached from a previous run.
    ready = sum(
        1 for p in args.output_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMG_EXTS
    )
    logger.info(
        "Done: %d new (img=%d, pdf_slices=%d), %d total ready in %s",
        img_count + pdf_slice_count, img_count, pdf_slice_count, ready, args.output_dir,
    )
    if ready == 0:
        logger.error("no prepared files available; aborting.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
