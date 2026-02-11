"""UDP pixel sender - streams Canvas data to the MatrixPortal S3 over WiFi.

Protocol (per packet):
  - Bytes 0-1: row number (uint16 big-endian), 0xFFFF = frame-done signal
  - Bytes 2+:  64 * 2 = 128 bytes of RGB565 pixel data (little-endian)

One packet per row, 64 packets per frame + 1 frame-done signal.
Packets are paced with a small delay to avoid overwhelming the board.
Board listens on UDP port 7777.
"""

import os
import socket
import struct
import time
from ledmatrix.canvas import Canvas

MATRIX_PORT = 7777
FRAME_DONE = 0xFFFF
ROW_DELAY = 0.001  # 1ms between row packets


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
        buf = canvas.get_buffer()
        width = canvas.width
        # Pre-allocate packet buffer: 2-byte header + row RGB565
        packet = bytearray(2 + width * 2)
        for y in range(canvas.height):
            # Header: row number big-endian
            packet[0] = (y >> 8) & 0xFF
            packet[1] = y & 0xFF
            # Convert RGB888 to RGB565 little-endian
            src = y * width * 3
            dst = 2
            for x in range(width):
                r = buf[src]
                g = buf[src + 1]
                b = buf[src + 2]
                val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                packet[dst] = val & 0xFF
                packet[dst + 1] = (val >> 8) & 0xFF
                src += 3
                dst += 2
            self.sock.sendto(packet, addr)
            time.sleep(ROW_DELAY)
        # Frame done signal
        self.sock.sendto(struct.pack(">H", FRAME_DONE), addr)

    def close(self) -> None:
        self.sock.close()
