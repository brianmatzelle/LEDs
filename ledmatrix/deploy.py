"""Deploy board code to the CIRCUITPY drive."""

import shutil
import sys
from pathlib import Path

CIRCUITPY_PATHS = [
    Path("/run/media/cowboy/CIRCUITPY"),
    Path("/media/cowboy/CIRCUITPY"),
    Path("/mnt/CIRCUITPY"),
]

BOARD_DIR = Path(__file__).parent.parent / "board"


def find_circuitpy() -> Path | None:
    for p in CIRCUITPY_PATHS:
        if p.is_dir():
            return p
    return None


def deploy(mode: str = "receiver") -> None:
    """Deploy board code to CIRCUITPY.

    Args:
        mode: "receiver" deploys the UDP receiver as code.py.
              "backup" copies current CIRCUITPY to board/backup/.
    """
    circuitpy = find_circuitpy()
    if circuitpy is None:
        print("ERROR: CIRCUITPY drive not found. Is the board plugged in?")
        print(f"Looked in: {', '.join(str(p) for p in CIRCUITPY_PATHS)}")
        sys.exit(1)

    print(f"Found CIRCUITPY at {circuitpy}")

    if mode == "backup":
        dest = BOARD_DIR / "backup"
        dest.mkdir(parents=True, exist_ok=True)
        for item in circuitpy.iterdir():
            src = circuitpy / item.name
            dst = dest / item.name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        print(f"Backed up CIRCUITPY to {dest}")
        return

    if mode == "receiver":
        src = BOARD_DIR / "receiver.py"
        dst = circuitpy / "code.py"
        print(f"Deploying {src.name} -> {dst}")
        shutil.copy2(src, dst)
        print("Deployed! Board will auto-reload.")
        return

    # Deploy a specific file as code.py
    src = Path(mode)
    if not src.exists():
        print(f"ERROR: File not found: {src}")
        sys.exit(1)
    dst = circuitpy / "code.py"
    print(f"Deploying {src} -> {dst}")
    shutil.copy2(src, dst)
    print("Deployed! Board will auto-reload.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "receiver"
    deploy(mode)
