"""UDP pixel sender - streams Canvas data to the MatrixPortal S3 over WiFi.

Protocol (per packet):
  - Bytes 0-1: row number (uint16 big-endian), 0xFFFF = frame-done signal
  - Bytes 2+:  64 * 2 = 128 bytes of RGB565 pixel data (little-endian)

One packet per row, 64 packets per frame + 1 frame-done signal.
Rows are sent in small bursts with pauses between them, because CircuitPython's
lwIP UDP receive mailbox defaults to only 6 packets (CONFIG_LWIP_UDP_RECVMBOX_SIZE).
Sending faster than the board can drain this mailbox causes silent packet drops,
visible as stale/missing rows on the display.

References:
  - lwIP UDP mailbox default (6): https://github.com/espressif/esp-idf/blob/master/components/lwip/Kconfig
  - ESP32 UDP packet bunching: https://forum.arduino.cc/t/esp32-wifi-udp-bunching-packets/1162055
  - CircuitPython socketpool: https://docs.circuitpython.org/en/9.2.x/shared-bindings/socketpool/index.html
  - RGB565 conversion with NumPy: https://barth-dev.de/about-rgb565-and-how-to-convert-into-it/

Board listens on UDP port 7777.
"""

import os
import socket
import struct
import time

import numpy as np

from ledmatrix.canvas import Canvas

MATRIX_PORT = 7777
FRAME_DONE = 0xFFFF
BURST_SIZE = 4  # Rows per burst (must fit in board's 6-packet UDP mailbox)
BURST_DELAY = 0.004  # Pause between bursts for board to drain mailbox
FRAME_DELAY = 0.005  # Post-frame delay for board display.refresh()


class Sender:
    """Streams canvas pixel data to the MatrixPortal S3 over UDP."""

    def __init__(self, host: str | None = None, port: int = MATRIX_PORT):
        self.host = host or os.environ.get("MATRIX_IP", "")
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.enabled = bool(self.host)
        if not self.enabled:
            print("[sender] No MATRIX_IP set, streaming disabled. Set MATRIX_IP env var to enable.")

    def send_frame(self, canvas: Canvas) -> None:
        """Send the entire canvas as individual row packets + frame-done."""
        if not self.enabled:
            return
        addr = (self.host, self.port)
        width = canvas.width
        height = canvas.height
        # Vectorized RGB888 -> RGB565 conversion (entire frame at once)
        rgb = np.frombuffer(canvas.buffer, dtype=np.uint8).reshape(height, width, 3)
        r = rgb[:, :, 0].astype(np.uint16)
        g = rgb[:, :, 1].astype(np.uint16)
        b = rgb[:, :, 2].astype(np.uint16)
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        frame_data = rgb565.astype('<u2').tobytes()
        # Send rows in bursts that fit the board's UDP receive mailbox
        packet = bytearray(2 + width * 2)
        row_bytes = width * 2
        for y in range(height):
            packet[0] = (y >> 8) & 0xFF
            packet[1] = y & 0xFF
            offset = y * row_bytes
            packet[2:] = frame_data[offset:offset + row_bytes]
            self.sock.sendto(packet, addr)
            if (y + 1) % BURST_SIZE == 0:
                time.sleep(BURST_DELAY)
        # Frame done signal, then wait for board to call display.refresh()
        self.sock.sendto(struct.pack(">H", FRAME_DONE), addr)
        time.sleep(FRAME_DELAY)

    def close(self) -> None:
        self.sock.close()
