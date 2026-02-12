# Board Buttons and the Demo Chooser

**2026-02-12**

Until today, picking a demo meant SSHing into my desktop and choosing from the `./run` menu. Fine for development, but not great when the matrix is across the room and you just want to flip through some eye candy. The MatrixPortal S3 has two physical buttons (Up and Down) that have been sitting there doing nothing. Today they got a job.

## The problem

The architecture is one-way: desktop renders frames, streams them over UDP to the board, board displays them. The board has never talked back. To use the physical buttons, we needed bidirectional communication.

## What changed

### Board firmware (`board/receiver.py`)

The receiver now initializes both buttons using `digitalio` with internal pull-ups (the hardware has none). After each row blit in the main receive loop, it polls the button state. On a press transition (not pressed -> pressed), it fires a 1-byte UDP packet back to the desktop sender's IP on port 7778:

- `0x01` = Up button
- `0x02` = Down button

The sender's IP comes for free -- it's the source address from `recvfrom_into()` on every incoming frame packet. A 250ms debounce cooldown prevents double-fires. The `sendto` reuses the existing receive socket (UDP sockets are bidirectional) and is wrapped in a try/except so a send failure can never crash the receiver.

This is fully backward-compatible. When running a normal demo, nobody listens on port 7778 and the button packets silently drop into the void.

### Demo Chooser app (`apps/chooser.py`)

A new app that auto-discovers every other app in `apps/`, imports their `render` functions, and cycles through them live. It runs its own main loop (same Canvas + Simulator + Sender pattern as the standard `run()`) with two extra input sources:

- **Board buttons**: A non-blocking UDP socket on port 7778 drains queued button events each frame.
- **Keyboard arrows**: `pygame.key.get_pressed()` detects Up/Down arrow keys in the simulator window, debounced with previous-state tracking. Useful for testing without the board.

When you switch demos, a centered overlay shows the demo name and position (e.g. "RAINBOW" / "3/7") for two seconds over a dark background bar, then fades away to let the demo render unobstructed.

Apps that fail to import (like `caesar.py` when its external ASCII file is missing) get skipped with a console warning. Apps whose `render()` throws at runtime show an "ERROR" screen and can be arrow-keyed past.

### Run script

Added "Chooser" as the 8th entry in the `./run` menu.

## Gotcha: firewall

The one snag was the desktop firewall blocking incoming UDP on port 7778. The board was sending button events correctly (verified via `make serial`), but they never reached the chooser. Fix:

```bash
sudo ufw allow 7778/udp
```

## How to use it

```bash
make deploy                          # Push button-aware receiver to board
make stream app=apps/chooser.py      # Run the chooser with board streaming
```

Or just `./run` and pick Chooser from the menu. Press Up/Down on the board to flip through demos.
