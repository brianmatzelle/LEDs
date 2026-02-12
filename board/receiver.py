# SPDX-License-Identifier: MIT
# UDP Pixel Receiver for MatrixPortal S3 + 64x64 HUB75 LED Matrix
#
# Connects to WiFi and listens for UDP pixel data on port 7777.
# Protocol: 2-byte row number (big-endian) + 128 bytes RGB565 (little-endian) per row.
#           Row 0xFFFF = frame-done signal (triggers display refresh).
#
# Desktop sender pre-converts RGB888 to RGB565 and paces packets.
# Board uses memoryview copy + arrayblit for zero per-pixel Python work.
#
# Pair with the desktop `ledmatrix` package sender:
#   MATRIX_IP=<board_ip> python apps/rainbow.py

import array
import time
import board
import digitalio
import bitmaptools
import displayio
import rgbmatrix
import framebufferio
import wifi
import socketpool
from os import getenv

# --- Configuration ---
MATRIX_WIDTH = int(getenv("MATRIX_WIDTH", "64"))
MATRIX_HEIGHT = int(getenv("MATRIX_HEIGHT", "64"))
UDP_PORT = 7777
FRAME_DONE = 0xFFFF
BUTTON_PORT = 7778
BTN_UP_CODE = 0x01
BTN_DOWN_CODE = 0x02
DEBOUNCE_S = 0.25

# --- WiFi ---
ssid = getenv("CIRCUITPY_WIFI_SSID")
password = getenv("CIRCUITPY_WIFI_PASSWORD")

if not ssid or not password:
    raise RuntimeError("Set CIRCUITPY_WIFI_SSID and CIRCUITPY_WIFI_PASSWORD in settings.toml")

print(f"Connecting to {ssid}...")
wifi.radio.connect(ssid, password)
ip = str(wifi.radio.ipv4_address)
print(f"Connected! IP: {ip}")
print(f"Listening for pixel data on UDP port {UDP_PORT}")
print(f"On your desktop, run: MATRIX_IP={ip} python apps/rainbow.py")

# --- Buttons ---
btn_up = digitalio.DigitalInOut(board.BUTTON_UP)
btn_up.direction = digitalio.Direction.INPUT
btn_up.pull = digitalio.Pull.UP

btn_down = digitalio.DigitalInOut(board.BUTTON_DOWN)
btn_down.direction = digitalio.Direction.INPUT
btn_down.pull = digitalio.Pull.UP

# --- Display setup ---
displayio.release_displays()

matrix = rgbmatrix.RGBMatrix(
    width=MATRIX_WIDTH,
    height=MATRIX_HEIGHT,
    bit_depth=4,
    rgb_pins=[
        board.MTX_B1, board.MTX_G1, board.MTX_R1,
        board.MTX_B2, board.MTX_G2, board.MTX_R2,
    ],
    addr_pins=[
        board.MTX_ADDRA, board.MTX_ADDRB, board.MTX_ADDRC, board.MTX_ADDRD,
        board.MTX_ADDRE,
    ],
    clock_pin=board.MTX_CLK,
    latch_pin=board.MTX_LAT,
    output_enable_pin=board.MTX_OE,
)

display = framebufferio.FramebufferDisplay(matrix, auto_refresh=False)

bitmap = displayio.Bitmap(MATRIX_WIDTH, MATRIX_HEIGHT, 65536)
pixel_shader = displayio.ColorConverter(input_colorspace=displayio.Colorspace.RGB565)
tile_grid = displayio.TileGrid(bitmap, pixel_shader=pixel_shader)
group = displayio.Group()
group.append(tile_grid)
display.root_group = group

# Pre-allocate row buffer and its byte-level memoryview for zero-copy receive
row_buf = array.array("H", [0] * MATRIX_WIDTH)
row_buf_bytes = memoryview(row_buf).cast("B")

# --- Show startup pattern (green border = ready) ---
GREEN = ((0 & 0xF8) << 8) | ((255 & 0xFC) << 3) | (0 >> 3)
for x in range(MATRIX_WIDTH):
    bitmap[x, 0] = GREEN
    bitmap[x, MATRIX_HEIGHT - 1] = GREEN
for y in range(MATRIX_HEIGHT):
    bitmap[0, y] = GREEN
    bitmap[MATRIX_WIDTH - 1, y] = GREEN
display.refresh()

# --- UDP socket ---
pool = socketpool.SocketPool(wifi.radio)
sock = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
sock.bind(("0.0.0.0", UDP_PORT))
sock.settimeout(None)

# Buffer for receiving packets: 2 byte header + 64*2 bytes RGB565 data
HEADER_SIZE = 2
ROW_BYTES = MATRIX_WIDTH * 2
PACKET_SIZE = HEADER_SIZE + ROW_BYTES
recv_buf = bytearray(PACKET_SIZE)
recv_mv = memoryview(recv_buf)

frame_count = 0
last_fps_time = time.monotonic()
sender_addr = None
btn_up_prev = False
btn_down_prev = False
last_btn_time = 0.0
btn_packet = bytearray(1)

# --- Main receive loop ---
while True:
    nbytes, addr = sock.recvfrom_into(recv_buf)
    sender_addr = addr[0]

    if nbytes < HEADER_SIZE:
        continue

    row_num = (recv_buf[0] << 8) | recv_buf[1]

    if row_num == FRAME_DONE:
        display.refresh()
        frame_count += 1
        now = time.monotonic()
        if now - last_fps_time >= 5.0:
            fps = frame_count / (now - last_fps_time)
            print(f"FPS: {fps:.1f}")
            frame_count = 0
            last_fps_time = now
        continue

    if row_num >= MATRIX_HEIGHT or nbytes < PACKET_SIZE:
        continue

    # C-level memcpy: copy RGB565 bytes into row buffer (no Python loop)
    row_buf_bytes[:] = recv_mv[HEADER_SIZE:HEADER_SIZE + ROW_BYTES]

    # Blit entire row in one C-level call
    bitmaptools.arrayblit(bitmap, row_buf, x1=0, y1=row_num, x2=MATRIX_WIDTH, y2=row_num + 1)

    # --- Button polling ---
    now = time.monotonic()
    up_pressed = not btn_up.value
    down_pressed = not btn_down.value

    if up_pressed and not btn_up_prev and (now - last_btn_time) > DEBOUNCE_S:
        btn_packet[0] = BTN_UP_CODE
        try:
            sock.sendto(btn_packet, (sender_addr, BUTTON_PORT))
        except Exception:
            pass
        last_btn_time = now

    if down_pressed and not btn_down_prev and (now - last_btn_time) > DEBOUNCE_S:
        btn_packet[0] = BTN_DOWN_CODE
        try:
            sock.sendto(btn_packet, (sender_addr, BUTTON_PORT))
        except Exception:
            pass
        last_btn_time = now

    btn_up_prev = up_pressed
    btn_down_prev = down_pressed
