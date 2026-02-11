"""Main run loop - ties together Canvas, Simulator, and Sender."""

import time
from ledmatrix.canvas import Canvas
from ledmatrix.simulator import Simulator
from ledmatrix.sender import Sender

# Callback type: fn(canvas, time_seconds, frame_number) -> None
RenderFn = type(lambda c, t, f: None)


def run(render: RenderFn, fps: int = 30, title: str = "LED Matrix Simulator",
        scale: int = 10, width: int = 64, height: int = 64) -> None:
    """Main entry point. Runs the render loop with simulator preview + optional board streaming.

    Args:
        render: Callback called each frame with (canvas, elapsed_time, frame_number).
                Draw to the canvas each frame. Canvas is NOT auto-cleared between frames.
        fps: Target frames per second (default 30).
        title: Window title.
        scale: Pixel scale factor for simulator window (default 10 = 640x640).
        width: Matrix width in pixels (default 64).
        height: Matrix height in pixels (default 64).
    """
    canvas = Canvas(width, height)
    sim = Simulator(canvas, scale=scale, title=title)
    sender = Sender()

    start = time.monotonic()
    frame = 0

    try:
        while True:
            t = time.monotonic() - start
            render(canvas, t, frame)

            if not sim.update():
                break

            sender.send_frame(canvas)
            sim.tick(fps)
            frame += 1
    except KeyboardInterrupt:
        pass
    finally:
        sender.close()
        sim.close()
