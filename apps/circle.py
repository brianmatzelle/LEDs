from ledmatrix import Canvas, run

def render(canvas: Canvas, t: float, frame: int) -> None:
    canvas.clear()
    # Pulsing radius
    r = int(10 + 8 * __import__('math').sin(t * 2))
    # Color-cycling circle
    color = canvas.hsv((t * 60) % 360)
    canvas.circle(32, 32, r, color, filled=True)

if __name__ == "__main__":
    run(render, fps=30, title="Circle")
