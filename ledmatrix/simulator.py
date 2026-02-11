"""Pygame-based LED matrix simulator. Shows a 10x upscaled preview of the canvas."""

import pygame
from ledmatrix.canvas import Canvas


class Simulator:
    """Opens a window that displays the Canvas contents, upscaled to be visible."""

    def __init__(self, canvas: Canvas, scale: int = 10, title: str = "LED Matrix Simulator"):
        self.canvas = canvas
        self.scale = scale
        self.width = canvas.width * scale
        self.height = canvas.height * scale

        pygame.init()
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption(title)
        self.clock = pygame.time.Clock()
        # Small surface at actual matrix resolution, then upscale
        self.surface = pygame.Surface((canvas.width, canvas.height))

    def update(self) -> bool:
        """Blit canvas to screen. Returns False if window was closed."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False

        # Write pixel buffer directly to the small surface
        buf = self.canvas.buffer
        w, h = self.canvas.width, self.canvas.height
        for y in range(h):
            for x in range(w):
                idx = (y * w + x) * 3
                self.surface.set_at((x, y), (buf[idx], buf[idx + 1], buf[idx + 2]))

        # Upscale to the display window
        pygame.transform.scale(self.surface, (self.width, self.height), self.screen)
        pygame.display.flip()
        return True

    def tick(self, fps: int = 30) -> None:
        """Limit framerate."""
        self.clock.tick(fps)

    def close(self) -> None:
        pygame.quit()
