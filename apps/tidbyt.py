"""Tidbyt Community App Runner - renders Tidbyt .star apps on the 64x64 matrix.

Discovers .star apps in tidbyt_apps/, renders them via the pixlet CLI,
and plays back the resulting GIF frames on the LED matrix canvas.

Controls:
  Left/Right arrows or board buttons: cycle between apps
  Up/Down arrows: cycle display mode (center, 2x scale, top, bottom)
"""

import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pygame
from PIL import Image

from ledmatrix import Canvas
from ledmatrix.simulator import Simulator
from ledmatrix.sender import Sender

TIDBYT_APPS_DIR = Path(__file__).parent.parent / "tidbyt_apps"
PIXLET_BIN = "pixlet"

# Display modes for 64x32 content on 64x64 canvas
DISPLAY_MODES = ["2x", "center", "top", "bottom"]


def discover_star_apps(apps_dir: Path) -> list[tuple[str, Path]]:
    """Find all .star files in subdirectories of apps_dir."""
    apps = []
    for star_file in sorted(apps_dir.rglob("*.star")):
        name = star_file.stem.replace("_", " ").title()
        apps.append((name, star_file))
    return apps


def render_star_app(star_path: Path, max_duration_ms: int = 15000) -> list[tuple[Image.Image, int]]:
    """Render a .star app via pixlet, return list of (PIL.Image, duration_ms) frames."""
    with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [PIXLET_BIN, "render", str(star_path), "--gif",
             "--output", tmp_path, "--silent",
             "--max_duration", str(max_duration_ms)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"[tidbyt] pixlet error: {result.stderr.strip()}")
            return []

        img = Image.open(tmp_path)
        frames = []
        try:
            while True:
                # Convert to RGB, ensure 64x32
                frame = img.convert("RGB")
                duration = img.info.get("duration", 50)
                frames.append((frame, duration))
                img.seek(img.tell() + 1)
        except EOFError:
            pass

        return frames
    except subprocess.TimeoutExpired:
        print("[tidbyt] pixlet render timed out")
        return []
    except Exception as e:
        print(f"[tidbyt] render error: {e}")
        return []
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def blit_frame(canvas: Canvas, frame: Image.Image, mode: str) -> None:
    """Draw a 64x32 PIL Image onto the 64x64 canvas using the given display mode."""
    canvas.clear()
    pixels = frame.load()
    fw, fh = frame.size  # should be 64x32

    if mode == "2x":
        # Scale 2x vertically: each source row maps to 2 canvas rows
        for y in range(fh):
            for x in range(fw):
                r, g, b = pixels[x, y]
                canvas.set(x, y * 2, (r, g, b))
                canvas.set(x, y * 2 + 1, (r, g, b))
    elif mode == "center":
        # Center vertically (16px offset)
        y_off = (canvas.height - fh) // 2
        for y in range(fh):
            for x in range(fw):
                r, g, b = pixels[x, y]
                canvas.set(x, y + y_off, (r, g, b))
    elif mode == "top":
        for y in range(fh):
            for x in range(fw):
                r, g, b = pixels[x, y]
                canvas.set(x, y, (r, g, b))
    elif mode == "bottom":
        y_off = canvas.height - fh
        for y in range(fh):
            for x in range(fw):
                r, g, b = pixels[x, y]
                canvas.set(x, y + y_off, (r, g, b))


class TidbytRunner:
    """Manages discovery, rendering, and playback of Tidbyt apps."""

    def __init__(self):
        self.apps = discover_star_apps(TIDBYT_APPS_DIR)
        if not self.apps:
            raise RuntimeError(f"No .star apps found in {TIDBYT_APPS_DIR}")

        self.current_app = 0
        self.display_mode = 0  # index into DISPLAY_MODES
        self.frames: list[tuple[Image.Image, int]] = []
        self.current_frame = 0
        self.frame_time = 0.0  # when to advance to next frame
        self.loading = False
        self.load_lock = threading.Lock()

        print(f"[tidbyt] Found {len(self.apps)} apps:")
        for i, (name, path) in enumerate(self.apps):
            print(f"  {i + 1}. {name}")

        # Render the first app
        self._render_current()

    def _render_current(self):
        """Render the current app in a background thread."""
        self.loading = True
        name, path = self.apps[self.current_app]
        print(f"[tidbyt] Rendering: {name}")

        def do_render():
            frames = render_star_app(path)
            with self.load_lock:
                self.frames = frames
                self.current_frame = 0
                self.frame_time = 0.0
                self.loading = False
            if frames:
                print(f"[tidbyt] Loaded {len(frames)} frames for {name}")
            else:
                print(f"[tidbyt] No frames rendered for {name}")

        thread = threading.Thread(target=do_render, daemon=True)
        thread.start()

    def switch_app(self, delta: int):
        """Switch to next/previous app."""
        self.current_app = (self.current_app + delta) % len(self.apps)
        self._render_current()

    def cycle_display_mode(self, delta: int):
        """Cycle display mode."""
        self.display_mode = (self.display_mode + delta) % len(DISPLAY_MODES)
        print(f"[tidbyt] Display mode: {DISPLAY_MODES[self.display_mode]}")

    def render(self, canvas: Canvas, t: float):
        """Render the current frame to the canvas."""
        with self.load_lock:
            if self.loading or not self.frames:
                # Show loading screen
                canvas.clear()
                name = self.apps[self.current_app][0]
                # Center the app name
                text = name.upper()[:15]
                tw = len(text) * 4 - 1
                tx = max(0, (64 - tw) // 2)
                canvas.text(tx, 24, text, (100, 100, 100))
                if self.loading:
                    dot_count = int(t * 3) % 4
                    canvas.text(28, 34, "." * dot_count, (60, 60, 60))
                else:
                    canvas.text(16, 34, "NO FRAMES", (255, 60, 60))
                return

            # Advance frame based on timing
            if self.frame_time == 0.0:
                self.frame_time = t

            frame_img, duration_ms = self.frames[self.current_frame]
            duration_s = duration_ms / 1000.0

            if t >= self.frame_time + duration_s:
                self.current_frame = (self.current_frame + 1) % len(self.frames)
                self.frame_time = t

            frame_img, _ = self.frames[self.current_frame]
            mode = DISPLAY_MODES[self.display_mode]
            blit_frame(canvas, frame_img, mode)


def main():
    runner = TidbytRunner()

    canvas = Canvas()
    sim = Simulator(canvas, title="Tidbyt Runner")
    sender = Sender()

    # Overlay state
    overlay_text = ""
    overlay_until = 0.0
    OVERLAY_DURATION = 2.0

    key_left_prev = False
    key_right_prev = False
    key_up_prev = False
    key_down_prev = False

    # Button listener (reuse pattern from chooser)
    import socket
    BUTTON_PORT = 7778
    btn_sock = None
    try:
        btn_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        btn_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        btn_sock.bind(("0.0.0.0", BUTTON_PORT))
        btn_sock.setblocking(False)
    except OSError:
        pass

    start = time.monotonic()

    def show_overlay():
        nonlocal overlay_text, overlay_until
        name = runner.apps[runner.current_app][0]
        mode = DISPLAY_MODES[runner.display_mode]
        overlay_text = name.upper()[:15]
        overlay_until = time.monotonic() + OVERLAY_DURATION

    show_overlay()

    try:
        while True:
            t = time.monotonic() - start
            now = time.monotonic()

            # Poll board buttons
            if btn_sock is not None:
                try:
                    while True:
                        data, _ = btn_sock.recvfrom(16)
                        if len(data) >= 1:
                            if data[0] == 0x01:  # UP = prev app
                                runner.switch_app(-1)
                                show_overlay()
                            elif data[0] == 0x02:  # DOWN = next app
                                runner.switch_app(1)
                                show_overlay()
                except BlockingIOError:
                    pass

            # Poll keyboard
            keys = pygame.key.get_pressed()

            key_left_now = keys[pygame.K_LEFT]
            key_right_now = keys[pygame.K_RIGHT]
            key_up_now = keys[pygame.K_UP]
            key_down_now = keys[pygame.K_DOWN]

            if key_left_now and not key_left_prev:
                runner.switch_app(-1)
                show_overlay()
            if key_right_now and not key_right_prev:
                runner.switch_app(1)
                show_overlay()
            if key_up_now and not key_up_prev:
                runner.cycle_display_mode(-1)
                show_overlay()
            if key_down_now and not key_down_prev:
                runner.cycle_display_mode(1)
                show_overlay()

            key_left_prev = key_left_now
            key_right_prev = key_right_now
            key_up_prev = key_up_now
            key_down_prev = key_down_now

            # Render current Tidbyt app
            runner.render(canvas, t)

            # Draw overlay
            if now < overlay_until:
                # Semi-transparent bar
                canvas.rect(0, 0, 64, 9, (0, 0, 0), filled=True)
                tw = len(overlay_text) * 4 - 1
                tx = max(0, (64 - tw) // 2)
                canvas.text(tx, 2, overlay_text, (255, 255, 255))
                # Show mode + index
                mode_str = DISPLAY_MODES[runner.display_mode].upper()
                idx_str = f"{runner.current_app + 1}/{len(runner.apps)} {mode_str}"
                iw = len(idx_str) * 4 - 1
                ix = max(0, (64 - iw) // 2)
                canvas.rect(0, 55, 64, 9, (0, 0, 0), filled=True)
                canvas.text(ix, 57, idx_str, (120, 120, 120))

            # Update display
            if not sim.update():
                break
            sender.send_frame(canvas)
            sim.tick(30)

    except KeyboardInterrupt:
        pass
    finally:
        if btn_sock is not None:
            btn_sock.close()
        sender.close()
        sim.close()


if __name__ == "__main__":
    main()
