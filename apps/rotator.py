"""App Rotator - auto-rotate through selected apps on a timer."""

import importlib.util
import socket
import sys
import time
from pathlib import Path

import pygame

from ledmatrix.canvas import Canvas
from ledmatrix.simulator import Simulator
from ledmatrix.sender import Sender

AUTO_ROTATE = 60.0
BUTTON_PORT = 7778
BTN_UP_CODE = 0x01
BTN_DOWN_CODE = 0x02
OVERLAY_DURATION = 2.0


def load_apps(filenames):
    """Import each file, return list of (name, render_fn) for those with render()."""
    apps = []
    for filename in filenames:
        path = Path(filename)
        if not path.exists():
            print(f"[rotator] File not found: {filename}")
            continue
        module_name = path.stem
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "render") and callable(mod.render):
                display_name = module_name.upper().replace("_", " ")
                apps.append((display_name, mod.render))
            else:
                print(f"[rotator] Skipping {path.name}: no render() function")
        except Exception as e:
            print(f"[rotator] Skipping {path.name}: {e}")
    return apps


def create_button_listener():
    """Create a non-blocking UDP socket listening for board button events."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", BUTTON_PORT))
        sock.setblocking(False)
        return sock
    except OSError as e:
        print(f"[rotator] Could not bind button listener on port {BUTTON_PORT}: {e}")
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python apps/rotator.py apps/rainbow.py apps/plasma.py ...")
        sys.exit(1)

    apps = load_apps(sys.argv[1:])
    if not apps:
        print("[rotator] No valid apps to rotate!")
        sys.exit(1)

    print(f"[rotator] Rotating {len(apps)} apps (every {int(AUTO_ROTATE)}s):")
    for i, (name, _) in enumerate(apps):
        print(f"  {i + 1}. {name}")

    canvas = Canvas()
    sim = Simulator(canvas, title="Rotator")
    sender = Sender()
    btn_sock = create_button_listener()

    current = 0
    overlay_name = apps[0][0]
    overlay_until = time.monotonic() + OVERLAY_DURATION
    last_switch = time.monotonic()

    key_up_prev = False
    key_down_prev = False

    start = time.monotonic()
    frame = 0
    fps = 30

    try:
        while True:
            t = time.monotonic() - start
            now = time.monotonic()
            switched = False

            # --- Poll board button events ---
            if btn_sock is not None:
                try:
                    while True:
                        data, _ = btn_sock.recvfrom(16)
                        if len(data) >= 1:
                            if data[0] == BTN_UP_CODE:
                                current = (current - 1) % len(apps)
                                switched = True
                            elif data[0] == BTN_DOWN_CODE:
                                current = (current + 1) % len(apps)
                                switched = True
                except BlockingIOError:
                    pass

            # --- Poll keyboard arrows ---
            keys = pygame.key.get_pressed()
            key_up_now = keys[pygame.K_UP]
            key_down_now = keys[pygame.K_DOWN]

            if key_up_now and not key_up_prev:
                current = (current - 1) % len(apps)
                switched = True

            if key_down_now and not key_down_prev:
                current = (current + 1) % len(apps)
                switched = True

            key_up_prev = key_up_now
            key_down_prev = key_down_now

            # --- Auto-rotate ---
            if not switched and len(apps) > 1 and now - last_switch >= AUTO_ROTATE:
                current = (current + 1) % len(apps)
                switched = True

            if switched:
                overlay_name = apps[current][0]
                overlay_until = now + OVERLAY_DURATION
                last_switch = now

            # --- Render current app ---
            name, render_fn = apps[current]
            try:
                render_fn(canvas, t, frame)
            except Exception:
                canvas.clear()
                canvas.text(4, 28, "ERROR", (255, 0, 0))
                canvas.text(4, 36, name[:10], (180, 180, 180))

            # --- Draw overlay ---
            if now < overlay_until:
                canvas.rect(0, 26, 64, 12, (0, 0, 0), filled=True)
                text_width = len(overlay_name) * 4 - 1
                text_x = max(0, (64 - text_width) // 2)
                canvas.text(text_x, 29, overlay_name, (255, 255, 255))
                idx_text = f"{current + 1}/{len(apps)}"
                idx_width = len(idx_text) * 4 - 1
                idx_x = max(0, (64 - idx_width) // 2)
                canvas.text(idx_x, 35, idx_text, (120, 120, 120))

            # --- Update display ---
            if not sim.update():
                break
            sender.send_frame(canvas)
            sim.tick(fps)
            frame += 1

    except KeyboardInterrupt:
        pass
    finally:
        if btn_sock is not None:
            btn_sock.close()
        sender.close()
        sim.close()


if __name__ == "__main__":
    main()
