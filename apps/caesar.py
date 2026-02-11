# apps/cube.py — Spinning ASCII 3D cube from crackGPT
#
# Parses the ASCII cube animation frames from the crackGPT web client,
# converts characters to brightness, scales to 64x64, and renders in green.

import math
import re
from ledmatrix import Canvas, run

ASCII_FILE = "/home/cowboy/projects/active/crackGPT/web-client/src/components/ascii.ts"

# ASCII density → brightness (0.0–1.0)
BRIGHTNESS = {
    " ": 0.0, "\xa0": 0.0,  # regular space + non-breaking space
    ".": 0.15, ":": 0.25, "-": 0.35, "+": 0.45,
    "*": 0.55, "=": 0.65, "%": 0.75, "#": 0.85, "@": 1.0,
}

# Frame timing (matches ChatInterface.tsx)
FRAME_MS = 180       # ms per frame
END_PAUSE_MS = 60000  # hold last frame
CHAR_ASPECT = 0.5    # monospace char width/height ratio


def _parse_frames():
    """Parse ASCII frames from TS file and pre-scale to 64x64 brightness grids."""
    with open(ASCII_FILE) as f:
        content = f.read()

    raw = re.findall(r"`\n(.*?)\n`", content, re.DOTALL)
    frames = []

    for text in raw:
        lines = text.split("\n")
        # Find bounding box of non-space content
        min_x, max_x = len(lines[0]) if lines else 0, 0
        min_y, max_y = len(lines), 0
        for y, line in enumerate(lines):
            for x, ch in enumerate(line):
                if ch not in (" ", "\xa0"):
                    min_x = min(min_x, x)
                    max_x = max(max_x, x + 1)
                    min_y = min(min_y, y)
                    max_y = max(max_y, y + 1)
        if max_x <= min_x or max_y <= min_y:
            continue

        src_w = max_x - min_x
        src_h = max_y - min_y

        # Build brightness grid for the cropped region
        grid = []
        for y in range(min_y, max_y):
            row = []
            line = lines[y] if y < len(lines) else ""
            for x in range(min_x, max_x):
                ch = line[x] if x < len(line) else " "
                row.append(BRIGHTNESS.get(ch, 0.5))
            grid.append(row)

        # Scale to 64x64 with aspect ratio correction
        frames.append(_scale(grid, src_w, src_h))

    return frames


def _scale(grid, src_w, src_h, size=64):
    """Area-average downsample to size×size with char aspect ratio correction."""
    # Visual dimensions accounting for monospace char proportions
    vis_w = src_w * CHAR_ASPECT
    vis_h = src_h
    scale = min(size / vis_w, size / vis_h)
    fit_w = max(1, int(vis_w * scale))
    fit_h = max(1, int(vis_h * scale))
    off_x = (size - fit_w) // 2
    off_y = (size - fit_h) // 2

    out = [[0.0] * size for _ in range(size)]

    for dy in range(fit_h):
        # Source y range for this destination row
        sy0 = dy * src_h / fit_h
        sy1 = (dy + 1) * src_h / fit_h
        iy0 = int(sy0)
        iy1 = min(int(math.ceil(sy1)), src_h)

        for dx in range(fit_w):
            # Source x range for this destination column
            sx0 = dx * src_w / fit_w
            sx1 = (dx + 1) * src_w / fit_w
            ix0 = int(sx0)
            ix1 = min(int(math.ceil(sx1)), src_w)

            # Area average
            total = 0.0
            count = 0
            for iy in range(iy0, iy1):
                for ix in range(ix0, ix1):
                    total += grid[iy][ix]
                    count += 1

            out[dy + off_y][dx + off_x] = total / count if count else 0.0

    return out


# Pre-compute all frames at import time
FRAMES = _parse_frames()
NUM_FRAMES = len(FRAMES)
FRAME_S = FRAME_MS / 1000.0
END_PAUSE_S = END_PAUSE_MS / 1000.0
CYCLE_S = (NUM_FRAMES - 1) * FRAME_S + END_PAUSE_S


def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()

    # Determine which ASCII frame to show
    t_cycle = t % CYCLE_S
    normal_duration = (NUM_FRAMES - 1) * FRAME_S
    if t_cycle < normal_duration:
        idx = int(t_cycle / FRAME_S)
    else:
        idx = NUM_FRAMES - 1

    brightness_grid = FRAMES[idx]

    for y in range(64):
        for x in range(64):
            b = brightness_grid[y][x]
            if b > 0.01:
                canvas.set(x, y, (0, int(b * 140), int(b * 30)))


if __name__ == "__main__":
    run(render, fps=30, title="Caesar")
