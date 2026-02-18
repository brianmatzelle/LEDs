"""Garvis voice assistant - LED matrix client with face + captions."""

import asyncio
import io
import json
import math
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

# Suppress PortAudio JACK errors when JACK isn't running
os.environ.setdefault("JACK_NO_START_SERVER", "1")

import numpy as np
import pygame
import sounddevice as sd
import websockets

from ledmatrix.canvas import Canvas
from ledmatrix.simulator import Simulator
from ledmatrix.sender import Sender

# --- Constants ---
BUTTON_PORT = 7778
BTN_UP_CODE = 0x01
BTN_DOWN_CODE = 0x02
SAMPLE_RATE_IN = 16000   # Mic capture: 16kHz mono 16-bit (what Garvis expects)
SAMPLE_RATE_OUT = 44100  # TTS playback: 44.1kHz (ElevenLabs MP3 default)
CHANNELS = 1
CHUNK_FRAMES = 1600      # 100ms of audio at 16kHz
RECONNECT_DELAY = 3.0

# --- Layout ---
EYE_Y = 13
LEFT_EYE_X = 18
RIGHT_EYE_X = 38
CAPTION_TOP = 34
CAPTION_LINE_H = 6       # 5px font + 1px gap
CAPTION_LINES = 5
CAPTION_CHARS = 15        # chars per line (4px each = 60px, 2px margin each side)
CAPTION_X = 2

# --- Colors ---
WHITE = (70, 70, 70)
DIM_WHITE = (45, 45, 45)
DIM_GRAY = (25, 25, 25)
CYAN = (0, 60, 70)
EYE_COLOR = (0, 80, 90)
EYE_DIM = (0, 30, 35)
MOUTH_COLOR = (0, 50, 60)
CAPTION_COLOR = (50, 55, 60)
SEP_COLOR = (15, 15, 15)
STATUS_CONNECTING = (60, 40, 0)
STATUS_IDLE = (0, 40, 0)
STATUS_LISTENING = (0, 50, 70)
STATUS_SPEAKING = (60, 0, 60)

# --- Config persistence ---
_config_path = Path(__file__).parent / ".garvis_config.json"


def _load_config() -> dict:
    if _config_path.exists():
        try:
            return json.loads(_config_path.read_text())
        except Exception:
            pass
    return {"host": "localhost:8000", "input_name": None, "output_name": None}


def _save_config(cfg: dict) -> None:
    _config_path.write_text(json.dumps(cfg, indent=2))


# ---------------------------------------------------------------------------
# Terminal submenu
# ---------------------------------------------------------------------------

def _list_input_devices() -> list[tuple[int, str]]:
    devices = sd.query_devices()
    result = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            result.append((i, d["name"]))
    return result


def _list_output_devices() -> list[tuple[int, str]]:
    devices = sd.query_devices()
    result = []
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0:
            result.append((i, d["name"]))
    return result


def _resolve_device(name: str | None, direction: str) -> int | None:
    """Resolve a saved device name to its current index, or None for system default."""
    if name is None:
        return None
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["name"] == name:
            if direction == "input" and d["max_input_channels"] > 0:
                return i
            if direction == "output" and d["max_output_channels"] > 0:
                return i
    return None


def _display_name(saved_name: str | None, direction: str) -> str:
    if saved_name is None:
        return "System default"
    idx = _resolve_device(saved_name, direction)
    if idx is not None:
        return f"{saved_name} (#{idx})"
    return f"{saved_name} (not found)"


def _submenu() -> dict:
    cfg = _load_config()

    while True:
        print("\n  GARVIS - LED MATRIX CLIENT")
        print("  " + "=" * 30)
        print(f"\n  Server:  {cfg['host']}")
        print(f"  Input:   {_display_name(cfg['input_name'], 'input')}")
        print(f"  Output:  {_display_name(cfg['output_name'], 'output')}")
        print("\n  Options:")
        print("    h) Set server host")
        print("    i) Select input device")
        print("    o) Select output device")
        print("    s) Start")
        print("    q) Quit")

        choice = input("\n  Choice: ").strip().lower()

        if choice == "h":
            host = input(f"  Server host [{cfg['host']}]: ").strip()
            if host:
                cfg["host"] = host
                _save_config(cfg)
                print(f"  Set server to {cfg['host']}")

        elif choice == "i":
            devices = _list_input_devices()
            if not devices:
                print("  No input devices found.")
                continue
            print("\n  Input devices:")
            print("    0) System default")
            for j, (idx, name) in enumerate(devices, 1):
                marker = "*" if cfg["input_name"] == name else " "
                print(f"   {marker}{j}) {name}")
            pick = input("\n  Pick a device: ").strip()
            try:
                p = int(pick)
                if p == 0:
                    cfg["input_name"] = None
                elif 1 <= p <= len(devices):
                    cfg["input_name"] = devices[p - 1][1]
                else:
                    print("  Invalid choice.")
                    continue
            except ValueError:
                continue
            _save_config(cfg)
            print(f"  Input: {_display_name(cfg['input_name'], 'input')}")

        elif choice == "o":
            devices = _list_output_devices()
            if not devices:
                print("  No output devices found.")
                continue
            print("\n  Output devices:")
            print("    0) System default")
            for j, (idx, name) in enumerate(devices, 1):
                marker = "*" if cfg["output_name"] == name else " "
                print(f"   {marker}{j}) {name}")
            pick = input("\n  Pick a device: ").strip()
            try:
                p = int(pick)
                if p == 0:
                    cfg["output_name"] = None
                elif 1 <= p <= len(devices):
                    cfg["output_name"] = devices[p - 1][1]
                else:
                    print("  Invalid choice.")
                    continue
            except ValueError:
                continue
            _save_config(cfg)
            print(f"  Output: {_display_name(cfg['output_name'], 'output')}")

        elif choice == "s":
            break

        elif choice == "q":
            sys.exit(0)

        else:
            print("  Invalid choice.")

    # Resolve names to current indices for the client
    resolved = dict(cfg)
    resolved["input_device"] = _resolve_device(cfg["input_name"], "input")
    resolved["output_device"] = _resolve_device(cfg["output_name"], "output")
    return resolved


# ---------------------------------------------------------------------------
# Audio playback helpers
# ---------------------------------------------------------------------------

def _decode_mp3(data: bytes) -> tuple[bytes, int] | None:
    """Decode MP3 bytes to raw PCM via ffmpeg."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0",
             "-f", "s16le", "-acodec", "pcm_s16le",
             "-ac", "1", "-ar", str(SAMPLE_RATE_OUT),
             "pipe:1"],
            input=data, capture_output=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout, SAMPLE_RATE_OUT
    except Exception:
        pass
    return None


def _parse_audio(data: bytes) -> tuple[bytes, int] | None:
    """Extract raw PCM and sample rate from WAV or MP3 bytes."""
    # Try WAV first
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            sr = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
            return pcm, sr
    except Exception:
        pass
    # Try MP3/other via ffmpeg
    return _decode_mp3(data)


# ---------------------------------------------------------------------------
# WebSocket + audio I/O (runs in background asyncio thread)
# ---------------------------------------------------------------------------

class GarvisClient:
    """Manages WebSocket connection, mic capture, and audio playback."""

    def __init__(self, host: str, input_device: int | None, output_device: int | None,
                 state: dict):
        self.host = host
        self.input_device = input_device
        self.output_device = output_device
        self.state = state
        self._audio_queue: queue.Queue[bytes] = queue.Queue()
        self._running = True
        self._ws = None
        self._playing = False       # True while speakers are actively outputting audio
        self._play_end_time = 0.0   # monotonic timestamp of last playback finish

    async def run(self):
        """Main async loop: connect, capture, receive."""
        while self._running:
            self.state["status"] = "connecting"
            self.state["caption"] = ""
            try:
                await self._session()
            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.InvalidURI,
                    OSError) as e:
                print(f"[garvis] Connection lost: {e}")
            except Exception as e:
                print(f"[garvis] Error: {e}")

            if self._running:
                self.state["status"] = "connecting"
                await asyncio.sleep(RECONNECT_DELAY)

    async def _session(self):
        uri = f"ws://{self.host}/ws/voice"
        print(f"[garvis] Connecting to {uri}...")

        async with websockets.connect(uri) as ws:
            self._ws = ws
            self.state["status"] = "idle"
            print("[garvis] Connected!")

            # Send start signal
            await ws.send(json.dumps({"type": "start"}))

            # Start mic capture in a separate thread
            mic_task = asyncio.create_task(self._mic_capture(ws))
            playback_thread = threading.Thread(
                target=self._playback_loop, daemon=True
            )
            playback_thread.start()

            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        # TTS audio data
                        self._handle_audio(message)
                    else:
                        # JSON control/transcript
                        self._handle_json(message)
            finally:
                mic_task.cancel()
                try:
                    await mic_task
                except asyncio.CancelledError:
                    pass
                self._ws = None

    def _handle_json(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "transcript":
            role = data.get("role", "")
            text = data.get("text", "")
            if role == "assistant":
                self.state["caption"] = text
            elif role == "user":
                self.state["user_text"] = text

        elif msg_type == "status":
            listening = data.get("listening", False)
            speaking = data.get("speaking", False)
            if speaking:
                self.state["status"] = "speaking"
            elif listening:
                self.state["status"] = "listening"
            else:
                self.state["status"] = "idle"

    def _handle_audio(self, data: bytes):
        """Queue incoming TTS audio for playback."""
        result = _parse_audio(data)
        if result:
            pcm, sr = result
            # Resample if needed to match output sample rate
            if sr != SAMPLE_RATE_OUT:
                samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
                n_out = int(len(samples) * SAMPLE_RATE_OUT / sr)
                resampled = np.interp(
                    np.linspace(0, len(samples), n_out, endpoint=False),
                    np.arange(len(samples)),
                    samples,
                ).astype(np.int16)
                pcm = resampled.tobytes()
            self._audio_queue.put(pcm)
        else:
            # Assume raw PCM 24kHz mono 16-bit
            self._audio_queue.put(data)

    def _playback_loop(self):
        """Blocking loop that plays queued audio through speakers."""
        # Query the output device's native sample rate
        dev_info = sd.query_devices(self.output_device)
        native_rate = int(dev_info["default_samplerate"])
        print(f"[garvis] Speaker open: resampling to {native_rate}Hz")
        try:
            while self._running:
                try:
                    pcm = self._audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if not pcm:
                    continue
                samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
                # Resample from SAMPLE_RATE_OUT to native device rate
                if native_rate != SAMPLE_RATE_OUT:
                    n_out = int(len(samples) * native_rate / SAMPLE_RATE_OUT)
                    samples = np.interp(
                        np.linspace(0, len(samples), n_out, endpoint=False),
                        np.arange(len(samples)),
                        samples,
                    )
                out = samples.astype(np.int16).reshape(-1, 1)
                try:
                    self._playing = True
                    sd.play(out, samplerate=native_rate,
                            device=self.output_device, blocking=True)
                except Exception as e:
                    print(f"[garvis] Playback error: {e}")
                finally:
                    # Check if more audio is queued; only clear _playing when queue drains
                    if self._audio_queue.empty():
                        self._playing = False
                        self._play_end_time = time.monotonic()
        except Exception as e:
            print(f"[garvis] Playback thread error: {e}")

    async def _mic_capture(self, ws):
        """Capture mic audio at device native rate, resample to 16kHz, send to WebSocket."""
        loop = asyncio.get_event_loop()

        # Use the device's default sample rate (many USB mics only support 44.1/48kHz)
        dev_info = sd.query_devices(self.input_device)
        native_rate = int(dev_info["default_samplerate"])
        ratio = native_rate / SAMPLE_RATE_IN  # e.g. 48000/16000 = 3.0
        blocksize = int(CHUNK_FRAMES * ratio)  # capture proportionally more frames

        def callback(indata, frames, time_info, status):
            if status:
                print(f"[garvis] Mic status: {status}")
            # Suppress mic while speakers are playing to prevent echo feedback
            if self._playing:
                return
            if self._play_end_time and (time.monotonic() - self._play_end_time) < 1.5:
                return
            # Resample from native rate down to 16kHz
            samples = indata[:, 0].astype(np.float32)
            n_out = int(len(samples) / ratio)
            resampled = np.interp(
                np.linspace(0, len(samples), n_out, endpoint=False),
                np.arange(len(samples)),
                samples,
            ).astype(np.int16)
            asyncio.run_coroutine_threadsafe(ws.send(resampled.tobytes()), loop)

        try:
            with sd.InputStream(
                samplerate=native_rate,
                channels=CHANNELS,
                dtype="int16",
                blocksize=blocksize,
                device=self.input_device,
                callback=callback,
            ):
                print(f"[garvis] Mic open: {native_rate}Hz -> {SAMPLE_RATE_IN}Hz")
                while self._running:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[garvis] Mic error: {e}")

    def stop(self):
        self._running = False


def _start_client_thread(cfg: dict, state: dict) -> GarvisClient:
    """Start the Garvis WebSocket client in a background thread."""
    client = GarvisClient(
        host=cfg["host"],
        input_device=cfg["input_device"],
        output_device=cfg["output_device"],
        state=state,
    )

    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(client.run())

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    return client


# ---------------------------------------------------------------------------
# Face drawing
# ---------------------------------------------------------------------------

def _draw_eye_closed(canvas: Canvas, cx: int, cy: int, color):
    """Draw a closed eye: horizontal line."""
    for x in range(cx - 3, cx + 4):
        canvas.set(x, cy, color)


def _draw_eye_open(canvas: Canvas, cx: int, cy: int, color, t: float):
    """Draw an open eye: small circle outline."""
    r = 4
    # Simple circle approximation for small radius
    for angle_i in range(32):
        a = angle_i * (2 * math.pi / 32)
        x = cx + round(r * math.cos(a))
        y = cy + round(r * math.sin(a))
        canvas.set(x, y, color)
    # Pupil
    canvas.set(cx, cy, color)
    canvas.set(cx + 1, cy, color)
    canvas.set(cx, cy + 1, color)
    canvas.set(cx + 1, cy + 1, color)


def _draw_eye_blink(canvas: Canvas, cx: int, cy: int, color, phase: float):
    """Draw eye in mid-blink (arc)."""
    if phase < 0.3:
        _draw_eye_open(canvas, cx, cy, color, 0)
    elif phase < 0.7:
        _draw_eye_closed(canvas, cx, cy, color)
    else:
        _draw_eye_open(canvas, cx, cy, color, 0)


def _draw_mouth(canvas: Canvas, t: float, color):
    """Draw an animated mouth (speaking)."""
    cx, cy = 32, 22
    # Width oscillates when speaking
    w = 3 + int(3 * abs(math.sin(t * 8)))
    for x in range(cx - w, cx + w + 1):
        canvas.set(x, cy, color)
        canvas.set(x, cy + 1, color)


def _draw_face(canvas: Canvas, status: str, t: float):
    """Draw the face on the top half based on current status."""
    if status == "connecting":
        # Dim pulsing eyes
        b = int(15 + 15 * abs(math.sin(t * 2)))
        dim = (0, b, b + 5)
        _draw_eye_closed(canvas, LEFT_EYE_X, EYE_Y, dim)
        _draw_eye_closed(canvas, RIGHT_EYE_X, EYE_Y, dim)
        # Pulsing dot
        dot_b = int(30 + 30 * abs(math.sin(t * 3)))
        canvas.set(32, 24, (dot_b, dot_b // 2, 0))

    elif status == "idle":
        # Closed eyes with occasional blink (open briefly)
        blink_cycle = t % 5.0  # blink every ~5 seconds
        if 4.6 < blink_cycle < 5.0:
            phase = (blink_cycle - 4.6) / 0.4
            _draw_eye_blink(canvas, LEFT_EYE_X, EYE_Y, EYE_COLOR, phase)
            _draw_eye_blink(canvas, RIGHT_EYE_X, EYE_Y, EYE_COLOR, phase)
        else:
            _draw_eye_closed(canvas, LEFT_EYE_X, EYE_Y, EYE_COLOR)
            _draw_eye_closed(canvas, RIGHT_EYE_X, EYE_Y, EYE_COLOR)

    elif status == "listening":
        # Open eyes, subtle pulse
        pulse = 0.8 + 0.2 * math.sin(t * 4)
        c = (0, int(80 * pulse), int(90 * pulse))
        _draw_eye_open(canvas, LEFT_EYE_X, EYE_Y, c, t)
        _draw_eye_open(canvas, RIGHT_EYE_X, EYE_Y, c, t)

    elif status == "speaking":
        # Open eyes + animated mouth
        _draw_eye_open(canvas, LEFT_EYE_X, EYE_Y, EYE_COLOR, t)
        _draw_eye_open(canvas, RIGHT_EYE_X, EYE_Y, EYE_COLOR, t)
        _draw_mouth(canvas, t, MOUTH_COLOR)


# ---------------------------------------------------------------------------
# Caption drawing
# ---------------------------------------------------------------------------

def _word_wrap(text: str, width: int) -> list[str]:
    """Wrap text to fit within `width` characters per line."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines if lines else [""]


def _draw_captions(canvas: Canvas, text: str, t: float):
    """Draw word-wrapped captions on the bottom half."""
    if not text:
        return

    lines = _word_wrap(text, CAPTION_CHARS)

    # Show last CAPTION_LINES lines (auto-scroll to bottom)
    visible = lines[-CAPTION_LINES:]

    for i, line in enumerate(visible):
        y = CAPTION_TOP + i * CAPTION_LINE_H
        canvas.text(CAPTION_X, y, line, CAPTION_COLOR)


# ---------------------------------------------------------------------------
# Status indicator
# ---------------------------------------------------------------------------

def _draw_status_dot(canvas: Canvas, status: str, t: float):
    """Small status dot in bottom-left corner."""
    colors = {
        "connecting": STATUS_CONNECTING,
        "idle": STATUS_IDLE,
        "listening": STATUS_LISTENING,
        "speaking": STATUS_SPEAKING,
    }
    base = colors.get(status, DIM_GRAY)
    pulse = 0.5 + 0.5 * abs(math.sin(t * 2))
    c = (int(base[0] * pulse), int(base[1] * pulse), int(base[2] * pulse))
    canvas.set(1, 62, c)


# ---------------------------------------------------------------------------
# Button listener
# ---------------------------------------------------------------------------

def _create_button_listener():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", BUTTON_PORT))
        sock.setblocking(False)
        return sock
    except OSError as e:
        print(f"[garvis] Could not bind button port {BUTTON_PORT}: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = _submenu()

    print(f"\n  Connecting to Garvis at {cfg['host']}...")
    print()

    # Shared state between display and client threads
    state = {
        "status": "connecting",
        "caption": "",
        "user_text": "",
    }

    client = _start_client_thread(cfg, state)

    # Display loop
    canvas = Canvas()
    sim = Simulator(canvas, title="Garvis")
    sender = Sender()
    btn_sock = _create_button_listener()

    start = time.monotonic()

    try:
        while True:
            t = time.monotonic() - start

            # Drain button events (reserved for future use)
            if btn_sock is not None:
                try:
                    while True:
                        btn_sock.recvfrom(16)
                except BlockingIOError:
                    pass

            # Render
            canvas.clear()

            # Separator line
            for x in range(64):
                canvas.set(x, 32, SEP_COLOR)

            # Face (top half)
            _draw_face(canvas, state["status"], t)

            # Captions (bottom half)
            _draw_captions(canvas, state["caption"], t)

            # Status dot
            _draw_status_dot(canvas, state["status"], t)

            # Update display
            if not sim.update():
                break
            sender.send_frame(canvas)
            sim.tick(20)

    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
        if btn_sock is not None:
            btn_sock.close()
        sender.close()
        sim.close()


if __name__ == "__main__":
    main()
