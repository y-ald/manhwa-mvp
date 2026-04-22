#!/usr/bin/env bash
# Turn each scan into a short animated segment via FFmpeg.
#
# Effects per image (kept intentionally simple but cinematic enough for an MVP):
#   - aggressive crop (focus on the action area)
#   - slow zoom-in (Ken Burns)
#   - contrast bump
#   - light film grain
#   - vignette
#
# Usage:
#   ./scripts/transform_images.sh <scans_dir> <out_segments_dir> \
#       <width> <height> <fps> <segment_duration>
set -euo pipefail

SCANS_DIR="${1:-storage/input/scans}"
OUT_DIR="${2:-storage/temp/video_segments}"
WIDTH="${3:-1280}"
HEIGHT="${4:-720}"
FPS="${5:-30}"
DURATION="${6:-3}"

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[transform_images] ERROR: ffmpeg not found in PATH." >&2
    exit 1
fi

if [[ ! -d "$SCANS_DIR" ]]; then
    echo "[transform_images] ERROR: scans dir not found: $SCANS_DIR" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/*.mp4

# Total zoompan frames.
TOTAL_FRAMES=$(( DURATION * FPS ))

# Match common image extensions (lowercase + uppercase).
shopt -s nullglob nocaseglob
mapfile -t IMAGES < <(find "$SCANS_DIR" -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) \
    | sort)
shopt -u nocaseglob

if [[ ${#IMAGES[@]} -eq 0 ]]; then
    echo "[transform_images] ERROR: no images found in $SCANS_DIR" >&2
    exit 1
fi

idx=0
for img in "${IMAGES[@]}"; do
    idx=$((idx + 1))
    out=$(printf "%s/seg_%04d.mp4" "$OUT_DIR" "$idx")
    echo "[transform_images] [$idx/${#IMAGES[@]}] $img -> $out"

    # Filter chain:
    #  1) scale to a working canvas larger than target (so crop has room)
    #  2) crop a tighter focus area
    #  3) zoompan slow zoom-in
    #  4) eq contrast
    #  5) noise grain
    #  6) vignette
    #  7) format yuv420p for max player compatibility
    ffmpeg -y -loop 1 -i "$img" \
      -vf "scale=${WIDTH}*1.4:${HEIGHT}*1.4:force_original_aspect_ratio=increase,\
crop=${WIDTH}*1.2:${HEIGHT}*1.2,\
zoompan=z='min(zoom+0.0010,1.15)':d=${TOTAL_FRAMES}:s=${WIDTH}x${HEIGHT}:fps=${FPS},\
eq=contrast=1.10:saturation=1.05,\
noise=alls=8:allf=t,\
vignette=PI/5,\
format=yuv420p" \
      -t "$DURATION" -r "$FPS" -c:v libx264 -preset veryfast -crf 23 \
      -movflags +faststart \
      "$out" \
      -loglevel error
done

echo "[transform_images] Done: $idx segment(s) in $OUT_DIR"
