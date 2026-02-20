#!/usr/bin/env python3
"""Record animated GIFs from each app by rendering frames headlessly.

Usage: python record_gifs.py
Output: media/demo-*.gif
"""

import math
import os
import sys

# Prevent pygame from opening windows or printing its banner
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from pathlib import Path
from PIL import Image

# Add project root to path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from ledmatrix.canvas import Canvas

MEDIA_DIR = ROOT / "media"
MEDIA_DIR.mkdir(exist_ok=True)

# GIF settings
SCALE = 6          # Upscale factor (64*6 = 384px)
DURATION_S = 4.0   # Seconds of animation per GIF
GIF_FPS = 20       # Frames per second in the GIF


def canvas_to_image(canvas: Canvas, scale: int = SCALE) -> Image.Image:
    """Convert a Canvas buffer to a scaled-up PIL Image."""
    img = Image.frombytes("RGB", (canvas.width, canvas.height), canvas.get_buffer())
    if scale > 1:
        img = img.resize(
            (canvas.width * scale, canvas.height * scale),
            Image.NEAREST,
        )
    return img


def render_gif(name: str, render_fn, fps: float = GIF_FPS,
               duration: float = DURATION_S, t_offset: float = 0.0):
    """Render frames and save as animated GIF."""
    out_path = MEDIA_DIR / f"demo-{name}.gif"
    n_frames = int(duration * fps)
    dt = 1.0 / fps
    frames = []

    canvas = Canvas()
    for i in range(n_frames):
        t = t_offset + i * dt
        canvas.clear()
        render_fn(canvas, t, i)
        frames.append(canvas_to_image(canvas))

    # Save as GIF (duration in ms per frame)
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=0,
        optimize=True,
    )
    print(f"  Saved {out_path} ({len(frames)} frames, {duration}s)")


# ---------------------------------------------------------------------------
# Simple apps: direct import + render
# ---------------------------------------------------------------------------

def record_rainbow():
    from apps.rainbow import render
    render_gif("rainbow", render)

def record_plasma():
    from apps.plasma import render
    render_gif("plasma", render, fps=15, duration=5.0)

def record_circle():
    from apps.circle import render
    render_gif("circle", render)

def record_hello():
    from apps.hello import render
    render_gif("hello", render)

def record_valentine():
    from apps.valentine import render
    render_gif("valentine", render)


# ---------------------------------------------------------------------------
# Caesar: depends on external file, try it
# ---------------------------------------------------------------------------

def record_caesar():
    try:
        from apps.caesar import render
        render_gif("caesar", render, duration=5.0)
    except FileNotFoundError:
        print("  Skipping caesar (external ASCII file not found)")


# ---------------------------------------------------------------------------
# G Train: mock the arrivals data so we get a realistic layout
# ---------------------------------------------------------------------------

def record_gtrain():
    # We need to prevent the background thread from starting, so
    # we build the render function manually using the app's drawing code
    import apps.gtrain as gtrain_mod
    # Inject mock data
    gtrain_mod.arrivals["north"] = [3, 8, 14]
    gtrain_mod.arrivals["south"] = [1, 6, 11]
    gtrain_mod.arrivals["updated"] = 999999.0  # pretend fresh

    def mock_render(canvas, t, frame):
        # Shift updated time so status dot stays green
        gtrain_mod.arrivals["updated"] = t - 1.0
        gtrain_mod.render(canvas, t, frame)

    render_gif("gtrain", mock_render)


# ---------------------------------------------------------------------------
# Sports: render mock game states (pre, live, final, no-game)
# ---------------------------------------------------------------------------

def _load_logo_from_disk(league: str, abbr: str, size: int = 22) -> list | None:
    """Load a cached logo PNG from disk into the pixel list format sports.py expects."""
    cache_file = ROOT / "apps" / ".logo_cache" / f"{league}_{abbr.lower()}_{size}.png"
    if not cache_file.exists():
        return None
    img = Image.open(cache_file).convert("RGB")
    pixels = []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = img.getpixel((x, y))
            r, g, b = int(r * 0.5), int(g * 0.5), int(b * 0.5)
            if r > 2 or g > 2 or b > 2:
                pixels.append((x, y, r, g, b))
    return pixels


def record_sports():
    from apps.sports import (
        _draw_pre_game, _draw_live_game, _draw_final, _draw_no_game,
        _draw_status_dot, _hex_to_led, _centered_text, _draw_logo,
        _get_logo, LOGO_Y, AWAY_LOGO_X, HOME_LOGO_X, ABBR_Y,
        DIM_WHITE, WHITE, DIM_GRAY, AMBER, LIVE_RED,
    )

    # Load cached logos directly from disk
    nyr_logo = _load_logo_from_disk("nhl", "nyr")
    phi_logo = _load_logo_from_disk("nhl", "phi")

    # Build a mock game data dict for a live game
    gd = {
        "state": "in",
        "home_abbr": "NYR",
        "away_abbr": "PHI",
        "home_score": "3",
        "away_score": "2",
        "home_logo": nyr_logo,
        "away_logo": phi_logo,
        "our_abbr": "NYR",
        "our_name": "NY RANGERS",
        "our_logo": nyr_logo,
        "sport": "hockey",
        "team_color": _hex_to_led("0038A8", 0.35),
        "team_accent": _hex_to_led("CE1126", 0.35),
        "period": 3,
        "clock": "8:42",
        "period_text": "P3",
        "status_detail": "",
        "detail": "",
        "game_date": "FEB 18",
        "game_time": "7:00 PM",
        "updated": 0.0,
    }

    def mock_render(canvas, t, frame):
        gd["updated"] = t - 1.0
        canvas.clear()
        _draw_live_game(canvas, gd, t)
        _draw_status_dot(canvas, gd, t)

    render_gif("sports", mock_render)


# ---------------------------------------------------------------------------
# Garvis: render animated face states (no websocket needed)
# ---------------------------------------------------------------------------

def record_garvis():
    from apps.garvis import (
        _draw_face, _draw_captions, _draw_status_dot,
        SEP_COLOR, CAPTION_COLOR,
    )

    # Cycle through states to show the face animation
    states = [
        ("idle", "", 2.0),
        ("listening", "", 1.5),
        ("speaking", "Hello! I am Garvis, your LED matrix assistant.", 3.0),
        ("idle", "Hello! I am Garvis, your LED matrix assistant.", 1.5),
    ]

    # Build a sequence of (state, caption, duration)
    total_dur = sum(s[2] for s in states)
    frames = []
    fps = GIF_FPS
    canvas = Canvas()

    t_global = 0.0
    for status, caption, dur in states:
        n = int(dur * fps)
        for i in range(n):
            t = t_global + i / fps
            canvas.clear()
            for x in range(64):
                canvas.set(x, 32, SEP_COLOR)
            _draw_face(canvas, status, t)
            _draw_captions(canvas, caption, t)
            _draw_status_dot(canvas, status, t)
            frames.append(canvas_to_image(canvas))
        t_global += dur

    out_path = MEDIA_DIR / "demo-garvis.gif"
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=0,
        optimize=True,
    )
    print(f"  Saved {out_path} ({len(frames)} frames, {total_dur}s)")


# ---------------------------------------------------------------------------
# Chooser: simulate cycling through demos with overlay
# ---------------------------------------------------------------------------

def record_chooser():
    from apps.rainbow import render as rainbow_render
    from apps.plasma import render as plasma_render
    from apps.circle import render as circle_render

    demos = [
        ("RAINBOW", rainbow_render),
        ("PLASMA", plasma_render),
        ("CIRCLE", circle_render),
    ]

    # Show each demo for ~2s with a 1s name overlay at the start
    canvas = Canvas()
    frames = []
    fps = GIF_FPS
    overlay_dur = 1.0
    demo_dur = 2.0
    t_global = 0.0

    for idx, (name, render_fn) in enumerate(demos):
        n = int(demo_dur * fps)
        for i in range(n):
            t = t_global + i / fps
            canvas.clear()
            render_fn(canvas, t, i)

            # Draw overlay for first overlay_dur seconds
            if i / fps < overlay_dur:
                canvas.rect(0, 24, 64, 16, (0, 0, 0), filled=True)
                # Center the name
                w = len(name) * 4 - 1
                x = (64 - w) // 2
                canvas.text(x, 27, name, Canvas.hsv(200, 0.8, 0.9))
                # Position indicator
                pos = f"{idx + 1}/{len(demos)}"
                pw = len(pos) * 4 - 1
                px = (64 - pw) // 2
                canvas.text(px, 34, pos, (35, 35, 35))

            frames.append(canvas_to_image(canvas))
        t_global += demo_dur

    out_path = MEDIA_DIR / "demo-chooser.gif"
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=0,
        optimize=True,
    )
    total = len(demos) * demo_dur
    print(f"  Saved {out_path} ({len(frames)} frames, {total}s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

RECORDINGS = [
    ("rainbow",   record_rainbow),
    ("plasma",    record_plasma),
    ("circle",    record_circle),
    ("hello",     record_hello),
    ("valentine", record_valentine),
    ("caesar",    record_caesar),
    ("gtrain",    record_gtrain),
    ("sports",    record_sports),
    ("garvis",    record_garvis),
    ("chooser",   record_chooser),
]


if __name__ == "__main__":
    print(f"\nRecording demo GIFs to {MEDIA_DIR}/\n")

    for name, fn in RECORDINGS:
        try:
            print(f"  Recording {name}...")
            fn()
        except Exception as e:
            print(f"  ERROR recording {name}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\nDone! GIFs saved to {MEDIA_DIR}/")
