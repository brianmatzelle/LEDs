# LED Matrix Development Environment

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
│ ├─ Simulator     │  RGB888 rows  │ ├─ RGB565 convert    │
│ └─ Sender        │               │ └─ HUB75 via rgbmatrix│
└─────────────────┘                └─────────────────────┘
```

### Desktop Side (`ledmatrix/` package)
- **canvas.py**: 64x64 RGB pixel buffer with drawing primitives (set, line, rect, circle, text, hsv)
- **simulator.py**: Pygame window showing 10x upscaled preview of the canvas
- **sender.py**: UDP streaming to the board (sends one packet per row + frame-done signal)
- **run.py**: Main loop tying canvas, simulator, and sender together
- **deploy.py**: Copies board code to CIRCUITPY USB drive

### Board Side (`board/`)
- **receiver.py**: UDP pixel receiver → HUB75 display via `rgbmatrix` + `displayio`
- Deployed to board as `code.py` via `make deploy`

### Apps (`apps/`)
- Python scripts that import `ledmatrix` and define a `render(canvas, t, frame)` function
- Run on the desktop, preview in simulator, optionally stream to board over WiFi

## Quick Reference

### Common Commands
```bash
source .venv/bin/activate          # Activate Python venv (required first)
make sim app=apps/rainbow.py       # Run app in simulator only
make stream app=apps/rainbow.py    # Run with simulator + stream to board
make deploy                        # Deploy receiver to board
make serial                        # Open serial console to board
make backup                        # Backup current CIRCUITPY contents
make mount                         # Mount CIRCUITPY USB drive
```

### Streaming to the Board
```bash
# 1. Deploy receiver to board (one-time, or after firmware changes)
make deploy

# 2. Check serial output for the board's IP address
make serial
# Output: "Connected! IP: 192.168.x.x"

# 3. Run an app with streaming enabled
MATRIX_IP=192.168.x.x make stream app=apps/rainbow.py
```

### Writing a New App
```python
# apps/myapp.py
from ledmatrix import Canvas, run

def render(canvas: Canvas, t: float, frame: int) -> None:
    """Called every frame. t = elapsed seconds, frame = frame count."""
    canvas.clear()
    canvas.set(10, 10, (255, 0, 0))                    # Red pixel
    canvas.rect(5, 5, 20, 10, (0, 0, 255))             # Blue rectangle
    canvas.line(0, 0, 63, 63, (255, 255, 0))           # Yellow diagonal
    canvas.circle(32, 32, 15, (0, 255, 0))             # Green circle outline
    canvas.circle(32, 32, 8, (255, 0, 255), filled=True)  # Magenta filled circle
    canvas.text(2, 58, "HI", canvas.hsv(t * 50 % 360)) # Color-cycling text
    canvas.text(20, 58, str(frame), (128, 128, 128))    # Frame counter

if __name__ == "__main__":
    run(render, fps=30, title="My App")
```

### Canvas API
```python
canvas.clear(color=(0,0,0))        # Fill with color (default black)
canvas.set(x, y, (r, g, b))       # Set pixel
canvas.get(x, y) -> (r, g, b)     # Get pixel
canvas.fill((r, g, b))            # Fill entire canvas
canvas.rect(x, y, w, h, color, filled=True)  # Rectangle
canvas.line(x0, y0, x1, y1, color)           # Bresenham line
canvas.circle(cx, cy, r, color, filled=False) # Circle
canvas.text(x, y, "TEXT", color)   # 3x5 pixel font (uppercase + digits)
Canvas.hsv(hue, sat, val)         # HSV to RGB (hue 0-360, s/v 0-1)
Canvas.hex(0xFF0000)              # Hex int to RGB tuple
Canvas.rgb(r, g, b)               # Clamped RGB tuple
canvas.get_buffer()                # Raw bytes (row-major RGB888)
canvas.get_row(y)                  # Raw bytes for one row (192 bytes)
```

## Board Details

### USB Detection
```bash
lsusb | grep Adafruit    # Should show: 239a:8126 Adafruit MatrixPortal S3
ls /dev/ttyACM0           # Serial port
udisksctl mount -b /dev/sdc1  # Mount CIRCUITPY (device may vary, check lsblk)
```

### CIRCUITPY Drive
- Mounted at `/run/media/cowboy/CIRCUITPY`
- `code.py` is the main entry point (auto-reloads on save)
- `settings.toml` contains WiFi credentials and matrix dimensions
- `lib/` contains CircuitPython library dependencies

### settings.toml (on board)
```toml
CIRCUITPY_WIFI_SSID = "your-network"
CIRCUITPY_WIFI_PASSWORD = "your-password"
MATRIX_WIDTH = 64
MATRIX_HEIGHT = 64
```

### CircuitPython Libraries Required (in CIRCUITPY/lib/)
For the UDP receiver, only built-in modules are needed (`wifi`, `socketpool`, `rgbmatrix`, `displayio`, `framebufferio`). No additional libraries required.

For the existing Mets app and other standalone apps, the following are installed:
`adafruit_matrixportal`, `adafruit_display_text`, `adafruit_bitmap_font`, `adafruit_bus_device`, `adafruit_io`, `adafruit_portalbase`, `adafruit_requests`, `neopixel`

### UDP Streaming Protocol
- Port: 7777
- Each packet: 2-byte row number (big-endian uint16) + 192 bytes RGB888 data (64 pixels x 3 bytes)
- Frame done signal: row number = 0xFFFF (2 bytes, no pixel data)
- 64 row packets + 1 frame-done = 65 packets per frame
- Board converts RGB888 → RGB565 and writes to `displayio.Bitmap`

### Matrix Pin Configuration (MatrixPortal S3)
```
RGB:     MTX_R1, MTX_G1, MTX_B1, MTX_R2, MTX_G2, MTX_B2
Address: MTX_ADDRA, MTX_ADDRB, MTX_ADDRC, MTX_ADDRD, MTX_ADDRE
Clock:   MTX_CLK
Latch:   MTX_LAT
OE:      MTX_OE
```

## File Structure
```
led-matrix/
├── CLAUDE.md           # This file
├── Makefile            # make sim, stream, deploy, serial, backup
├── pyproject.toml      # Python project config
├── .venv/              # Python virtual environment
├── ledmatrix/          # Desktop Python package
│   ├── __init__.py     # Exports Canvas and run
│   ├── canvas.py       # 64x64 pixel buffer + drawing primitives
│   ├── simulator.py    # Pygame preview window (10x upscale)
│   ├── sender.py       # UDP pixel streaming to board
│   ├── run.py          # Main render loop
│   └── deploy.py       # Deploy to CIRCUITPY
├── board/              # CircuitPython code for the MatrixPortal S3
│   ├── receiver.py     # UDP receiver → HUB75 display (deployed as code.py)
│   └── backup/         # Backup of original CIRCUITPY contents
├── apps/               # Demo apps (run on desktop)
│   ├── rainbow.py      # Scrolling rainbow wave
│   ├── hello.py        # Bouncing text
│   └── plasma.py       # Classic plasma effect
└── scripts/            # Utility scripts
```

## Troubleshooting

### No /dev/ttyACM0
- Check `lsusb` for the Adafruit device
- Run `lsmod | grep cdc_acm` -- if empty, the kernel module isn't loaded
- May need reboot if kernel was updated (modules must match running kernel)

### CIRCUITPY not mounting
- Double-tap Reset button to enter UF2 bootloader (MATRXS3BOOT drive appears)
- If already in CircuitPython, use `udisksctl mount -b /dev/sdX1` (check `lsblk` for device)

### Board not receiving UDP packets
- Check board serial output for IP address (`make serial`)
- Verify board and desktop are on the same WiFi network
- Check firewall: `sudo iptables -L` (UDP port 7777 must be open outbound)
- Test connectivity: `ping <board_ip>`

### Display shows wrong colors / garbled
- Check `bit_depth` in receiver.py (4 is the default, lower = fewer colors but more stable)
- Verify Address E jumper is soldered for 64x64 panels
- Check `MATRIX_WIDTH` and `MATRIX_HEIGHT` in settings.toml
