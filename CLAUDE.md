# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Hardware

- **Board**: Adafruit MatrixPortal S3 (ESP32-S3, 8MB flash, 2MB PSRAM, WiFi)
- **Display**: 64x64 HUB75 RGB LED Matrix (Adafruit product #5362)
- **Connection**: USB-C to host PC, HUB75 ribbon cable to LED panel
- **Address E jumper**: Soldered (required for 64-row panels)
- **Board firmware**: CircuitPython 9.2.8

## Architecture

Two-tier system: **desktop rendering** (Python/pygame) + **board display** (CircuitPython/WiFi).

```
Desktop (Python)                    Board (CircuitPython)
┌─────────────────┐    UDP/WiFi    ┌─────────────────────┐
│ App (apps/*.py)  │──────────────▶│ receiver.py (code.py)│
│ ├─ Canvas API    │  port 7777    │ ├─ WiFi listener     │
│ ├─ Simulator     │  RGB565 rows  │ ├─ arrayblit display │
│ └─ Sender        │◀──────────────│ ├─ HUB75 via rgbmatrix│
└─────────────────┘  port 7778     │ └─ Button events     │
                     button codes  └─────────────────────┘
```

### Desktop Side (`ledmatrix/` package)
- **canvas.py**: 64x64 RGB888 pixel buffer with drawing primitives (set, line, rect, circle, text, hsv). Buffer is a flat `bytearray` indexed as `(y * width + x) * 3`.
- **simulator.py**: Pygame window showing 10x upscaled preview of the canvas
- **sender.py**: Converts RGB888→RGB565 via numpy and streams via UDP to the board (one packet per row + frame-done signal). Rows sent in bursts of 4 with 4ms pauses to avoid overflowing the board's 6-packet UDP mailbox. Enabled only when `MATRIX_IP` env var is set.
- **run.py**: Main loop tying canvas, simulator, and sender together. **Canvas is NOT auto-cleared between frames**—the app's `render()` function controls clearing.
- **deploy.py**: Copies board code to CIRCUITPY USB drive

### Board Side (`board/`)
- **receiver.py**: UDP listener → `memoryview` copy + `bitmaptools.arrayblit()` → HUB75 display. Zero per-pixel Python work. Also polls physical buttons (BUTTON_UP/BUTTON_DOWN) and sends press events back to the desktop on UDP port 7778. Deployed to board as `code.py` via `make deploy`.

### Apps (`apps/`)
- Python scripts that import `ledmatrix` and define a `render(canvas, t, frame)` function
- Run on the desktop, preview in simulator, optionally stream to board over WiFi
- **chooser.py** is a meta-app: dynamically imports all other apps and lets you cycle through them with board buttons (port 7778) or keyboard arrows
- Apps can use background threads for async data (e.g., `gtrain.py` polls MTA feed in a daemon thread)
- **garvis.py** is a non-standard app — it manages its own Canvas/Simulator/Sender loop (does NOT use `ledmatrix.run()`). It runs a WebSocket client in a background asyncio thread, captures mic audio, plays TTS audio, and renders a face + captions.

### Garvis Voice Pipeline (`server/` + `apps/garvis.py`)
A voice assistant system with two components:
- **server/garvis_server.py**: FastAPI WebSocket server (`/ws/voice`). Pipeline: Deepgram STT → LLM (OpenClaw or Claude) → ElevenLabs TTS. Streams audio back to client as MP3 chunks. Has assistant mode (wake word "garvis") and always-respond mode.
- **apps/garvis.py**: LED matrix client that connects to the server, captures mic via sounddevice, decodes TTS audio via ffmpeg, renders animated face (eyes/mouth) + word-wrapped captions.

## Commands

```bash
source .venv/bin/activate          # Activate Python venv (required first)
make setup                         # Create .venv and install ledmatrix (editable)
make sim app=apps/rainbow.py       # Run app in simulator only
make stream app=apps/rainbow.py    # Run with simulator + stream to board
make list                          # List available apps
make deploy                        # Deploy receiver to board as code.py
make deploy-file file=board/foo.py # Deploy specific file as code.py
make serial                        # Open serial console to board
make backup                        # Backup current CIRCUITPY contents
make mount                         # Mount CIRCUITPY USB drive
./run                              # Interactive TUI menu to pick and run apps
```

`MATRIX_IP` defaults to `192.168.1.184` in the Makefile. Override with env var. When `MATRIX_IP` is unset (i.e., `make sim`), the sender silently disables itself.

### Streaming to the Board
```bash
make deploy                                    # One-time: deploy receiver
make serial                                    # Check board IP address
MATRIX_IP=192.168.x.x make stream app=apps/rainbow.py
```

### Garvis Voice Server
Requires the `[server]` optional dependencies and API keys in `.env`:
```bash
pip install -e ".[server]"         # Install server dependencies (fastapi, uvicorn, etc.)
# Also requires system package: ffmpeg (for garvis client audio decoding)

# Required in .env (project root or server/ directory):
# DEEPGRAM_API_KEY=...
# ELEVENLABS_API_KEY=...
# OPENCLAW_GATEWAY_URL=... and OPENCLAW_GATEWAY_TOKEN=...  (default LLM)
# — OR set USE_OPENCLAW=false and ANTHROPIC_API_KEY=...    (direct Claude)

python server/garvis_server.py     # Start server (ws://0.0.0.0:8000/ws/voice)
./run                              # Then pick "g" for Garvis server submenu
make sim app=apps/garvis.py        # Or run the client directly
```

## Writing a New App

```python
# apps/myapp.py
from ledmatrix import Canvas, run

def render(canvas: Canvas, t: float, frame: int) -> None:
    """Called every frame. t = elapsed seconds, frame = frame count."""
    canvas.clear()
    canvas.set(10, 10, (255, 0, 0))
    canvas.circle(32, 32, 15, (0, 255, 0))
    canvas.circle(32, 32, 8, (255, 0, 255), filled=True)
    canvas.text(2, 58, "HI", canvas.hsv(t * 50 % 360))

if __name__ == "__main__":
    run(render, fps=30, title="My App")
```

### Canvas API

```python
canvas.clear(color=(0,0,0))        # Fill with color (default black)
canvas.set(x, y, (r, g, b))       # Set pixel (bounds-checked)
canvas.get(x, y) -> (r, g, b)     # Get pixel (returns (0,0,0) out-of-bounds)
canvas.fill((r, g, b))            # Fill entire canvas
canvas.rect(x, y, w, h, color, filled=True)  # Rectangle
canvas.line(x0, y0, x1, y1, color)           # Bresenham line
canvas.circle(cx, cy, r, color, filled=False) # Midpoint circle
canvas.text(x, y, "TEXT", color)   # 3x5 pixel font (uppercase + digits + punctuation)
Canvas.hsv(hue, sat, val)         # HSV to RGB (hue 0-360, s/v 0-1)
Canvas.hex(0xFF0000)              # Hex int to RGB tuple
Canvas.rgb(r, g, b)               # Clamped RGB tuple
canvas.get_buffer()                # Raw bytes (row-major RGB888, 12288 bytes)
canvas.get_row(y)                  # Raw bytes for one row (192 bytes)
```

## UDP Protocol

### Pixel Streaming (port 7777, desktop → board)
- Each packet: 2-byte row number (big-endian uint16) + 128 bytes RGB565 data (64 pixels x 2 bytes, little-endian)
- Frame done signal: row number = 0xFFFF (2 bytes, no pixel data)
- 64 row packets + 1 frame-done = 65 packets per frame, sent in bursts of 4 rows with 4ms inter-burst delay
- RGB888→RGB565: `((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)`

### Button Events (port 7778, board → desktop)
- 1-byte packet: `0x01` = UP button, `0x02` = DOWN button
- Board debounces at 250ms; sends to the last-seen sender IP
- Desktop listens with a non-blocking UDP socket (see `chooser.py` for example)

## Board Details

### CIRCUITPY Drive
- Mounted at `/run/media/cowboy/CIRCUITPY`
- `code.py` is the main entry point (auto-reloads on save)
- `settings.toml` contains WiFi credentials (`CIRCUITPY_WIFI_SSID`, `CIRCUITPY_WIFI_PASSWORD`) and matrix dimensions (`MATRIX_WIDTH`, `MATRIX_HEIGHT`)
- Only built-in CircuitPython modules needed for the receiver (`wifi`, `socketpool`, `rgbmatrix`, `displayio`, `framebufferio`, `bitmaptools`)

### Display Config
- `bit_depth=4` in rgbmatrix (fewer colors but stable on ESP32-S3)
- Bitmap uses 65536-color RGB565 colorspace
- `auto_refresh=False` — display refreshes only on frame-done signal

## Troubleshooting

- **No /dev/ttyACM0**: Check `lsusb` for Adafruit device. May need reboot if kernel was updated (modules must match running kernel).
- **CIRCUITPY not mounting**: Double-tap Reset for UF2 bootloader. Otherwise `udisksctl mount -b /dev/sdX1` (check `lsblk`).
- **Board not receiving packets**: Check serial for IP (`make serial`). Verify same WiFi network. Check firewall on UDP 7777.
- **Wrong colors / garbled**: Check `bit_depth` in receiver.py. Verify Address E jumper soldered. Check `MATRIX_WIDTH`/`MATRIX_HEIGHT` in settings.toml.
