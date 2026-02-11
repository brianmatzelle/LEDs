"""64x64 RGB pixel buffer with drawing primitives."""

import colorsys
import math

# Type alias for RGB tuples
Color = tuple[int, int, int]

# Simple 3x5 bitmap font for digits and basic ASCII (space through ~)
# Each char is 3 pixels wide, 5 pixels tall, stored as 5 rows of 3-bit bitmaps
_FONT_3X5 = {
    ' ': [0b000, 0b000, 0b000, 0b000, 0b000],
    '!': [0b010, 0b010, 0b010, 0b000, 0b010],
    '0': [0b111, 0b101, 0b101, 0b101, 0b111],
    '1': [0b010, 0b110, 0b010, 0b010, 0b111],
    '2': [0b111, 0b001, 0b111, 0b100, 0b111],
    '3': [0b111, 0b001, 0b111, 0b001, 0b111],
    '4': [0b101, 0b101, 0b111, 0b001, 0b001],
    '5': [0b111, 0b100, 0b111, 0b001, 0b111],
    '6': [0b111, 0b100, 0b111, 0b101, 0b111],
    '7': [0b111, 0b001, 0b010, 0b010, 0b010],
    '8': [0b111, 0b101, 0b111, 0b101, 0b111],
    '9': [0b111, 0b101, 0b111, 0b001, 0b111],
    ':': [0b000, 0b010, 0b000, 0b010, 0b000],
    '.': [0b000, 0b000, 0b000, 0b000, 0b010],
    '-': [0b000, 0b000, 0b111, 0b000, 0b000],
    '+': [0b000, 0b010, 0b111, 0b010, 0b000],
    'A': [0b010, 0b101, 0b111, 0b101, 0b101],
    'B': [0b110, 0b101, 0b110, 0b101, 0b110],
    'C': [0b011, 0b100, 0b100, 0b100, 0b011],
    'D': [0b110, 0b101, 0b101, 0b101, 0b110],
    'E': [0b111, 0b100, 0b110, 0b100, 0b111],
    'F': [0b111, 0b100, 0b110, 0b100, 0b100],
    'G': [0b011, 0b100, 0b101, 0b101, 0b011],
    'H': [0b101, 0b101, 0b111, 0b101, 0b101],
    'I': [0b111, 0b010, 0b010, 0b010, 0b111],
    'J': [0b001, 0b001, 0b001, 0b101, 0b010],
    'K': [0b101, 0b110, 0b100, 0b110, 0b101],
    'L': [0b100, 0b100, 0b100, 0b100, 0b111],
    'M': [0b101, 0b111, 0b111, 0b101, 0b101],
    'N': [0b101, 0b111, 0b111, 0b111, 0b101],
    'O': [0b010, 0b101, 0b101, 0b101, 0b010],
    'P': [0b110, 0b101, 0b110, 0b100, 0b100],
    'Q': [0b010, 0b101, 0b101, 0b111, 0b011],
    'R': [0b110, 0b101, 0b110, 0b101, 0b101],
    'S': [0b011, 0b100, 0b010, 0b001, 0b110],
    'T': [0b111, 0b010, 0b010, 0b010, 0b010],
    'U': [0b101, 0b101, 0b101, 0b101, 0b111],
    'V': [0b101, 0b101, 0b101, 0b101, 0b010],
    'W': [0b101, 0b101, 0b111, 0b111, 0b101],
    'X': [0b101, 0b101, 0b010, 0b101, 0b101],
    'Y': [0b101, 0b101, 0b010, 0b010, 0b010],
    'Z': [0b111, 0b001, 0b010, 0b100, 0b111],
}


class Canvas:
    """64x64 RGB pixel buffer with drawing primitives.

    Pixels are stored as a flat bytearray in RGB order: [R0,G0,B0, R1,G1,B1, ...]
    Row-major: pixel (x, y) is at index (y * width + x) * 3.
    """

    def __init__(self, width: int = 64, height: int = 64):
        self.width = width
        self.height = height
        self.buffer = bytearray(width * height * 3)

    def clear(self, color: Color = (0, 0, 0)) -> None:
        """Fill entire canvas with a color (default black)."""
        if color == (0, 0, 0):
            self.buffer[:] = b'\x00' * len(self.buffer)
        else:
            r, g, b = color
            for i in range(0, len(self.buffer), 3):
                self.buffer[i] = r
                self.buffer[i + 1] = g
                self.buffer[i + 2] = b

    def set(self, x: int, y: int, color: Color) -> None:
        """Set a single pixel. Out-of-bounds writes are silently ignored."""
        if 0 <= x < self.width and 0 <= y < self.height:
            idx = (y * self.width + x) * 3
            self.buffer[idx] = color[0]
            self.buffer[idx + 1] = color[1]
            self.buffer[idx + 2] = color[2]

    def get(self, x: int, y: int) -> Color:
        """Get a pixel's color. Returns (0,0,0) for out-of-bounds."""
        if 0 <= x < self.width and 0 <= y < self.height:
            idx = (y * self.width + x) * 3
            return (self.buffer[idx], self.buffer[idx + 1], self.buffer[idx + 2])
        return (0, 0, 0)

    def fill(self, color: Color) -> None:
        """Alias for clear() with a color."""
        self.clear(color)

    def rect(self, x: int, y: int, w: int, h: int, color: Color, filled: bool = True) -> None:
        """Draw a rectangle. If filled=False, draws outline only."""
        if filled:
            for py in range(y, y + h):
                for px in range(x, x + w):
                    self.set(px, py, color)
        else:
            self.line(x, y, x + w - 1, y, color)
            self.line(x, y + h - 1, x + w - 1, y + h - 1, color)
            self.line(x, y, x, y + h - 1, color)
            self.line(x + w - 1, y, x + w - 1, y + h - 1, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        """Draw a line using Bresenham's algorithm."""
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.set(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def circle(self, cx: int, cy: int, r: int, color: Color, filled: bool = False) -> None:
        """Draw a circle using midpoint algorithm."""
        x = r
        y = 0
        err = 1 - r
        while x >= y:
            if filled:
                self.line(cx - x, cy + y, cx + x, cy + y, color)
                self.line(cx - x, cy - y, cx + x, cy - y, color)
                self.line(cx - y, cy + x, cx + y, cy + x, color)
                self.line(cx - y, cy - x, cx + y, cy - x, color)
            else:
                for px, py in [
                    (cx + x, cy + y), (cx - x, cy + y),
                    (cx + x, cy - y), (cx - x, cy - y),
                    (cx + y, cy + x), (cx - y, cy + x),
                    (cx + y, cy - x), (cx - y, cy - x),
                ]:
                    self.set(px, py, color)
            y += 1
            if err < 0:
                err += 2 * y + 1
            else:
                x -= 1
                err += 2 * (y - x) + 1

    def text(self, x: int, y: int, string: str, color: Color, spacing: int = 1) -> None:
        """Draw text using built-in 3x5 pixel font. Uppercase only."""
        cursor_x = x
        for ch in string.upper():
            glyph = _FONT_3X5.get(ch)
            if glyph is None:
                cursor_x += 3 + spacing
                continue
            for row_idx, row_bits in enumerate(glyph):
                for col in range(3):
                    if row_bits & (1 << (2 - col)):
                        self.set(cursor_x + col, y + row_idx, color)
            cursor_x += 3 + spacing

    @staticmethod
    def hsv(h: float, s: float = 1.0, v: float = 1.0) -> Color:
        """Convert HSV to RGB color tuple. h is 0-360, s and v are 0-1."""
        r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
        return (int(r * 255), int(g * 255), int(b * 255))

    @staticmethod
    def rgb(r: int, g: int, b: int) -> Color:
        """Convenience: clamp and return an RGB tuple."""
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

    @staticmethod
    def hex(color: int) -> Color:
        """Convert 0xRRGGBB integer to (R, G, B) tuple."""
        return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)

    def get_row(self, y: int) -> bytes:
        """Get raw RGB bytes for a single row."""
        start = y * self.width * 3
        return bytes(self.buffer[start:start + self.width * 3])

    def get_buffer(self) -> bytes:
        """Get the entire pixel buffer as bytes."""
        return bytes(self.buffer)
