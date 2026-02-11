"""Rainbow wave demo - scrolling diagonal rainbow pattern."""

from ledmatrix import Canvas, run


def render(canvas: Canvas, t: float, frame: int) -> None:
    for y in range(canvas.height):
        for x in range(canvas.width):
            hue = ((x + y) * 2.8 + t * 60) % 360
            canvas.set(x, y, canvas.hsv(hue, 1.0, 0.8))


if __name__ == "__main__":
    run(render, fps=30, title="Rainbow Wave")
