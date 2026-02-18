# Garvis on the LED Matrix

**2026-02-12**

Garvis is our Discord voice assistant (Deepgram STT, Claude LLM, ElevenLabs TTS). I wanted to run it standalone on a Raspberry Pi with a mic and speakers -- no Discord, no laptop, just talk to the LED panel on the shelf. That meant building two things: a lightweight voice server and a new LED matrix client.

## Architecture: keep compute off the board

The MatrixPortal S3 is a pixel pusher, not a compute platform. The Pi does everything: captures audio, runs the voice pipeline server, renders frames, and streams pixels to the board over WiFi. The data flow is:

```
Mic → Pi (garvis client) → WebSocket → Pi (garvis server)
                                           ↓
                          Deepgram STT → Claude → ElevenLabs TTS
                                           ↓
Pi (garvis client) ← WebSocket ← MP3 audio + JSON transcripts
  ↓            ↓
Speakers    LED Matrix (face + captions)
```

Two processes on the Pi: the server (`./run` → `g`) and the client (`./run` → `9`).

## Copying the server

The main Garvis project lives in a separate repo with Discord bot code, MCP tools, local model support, and a lot of config. The LED matrix doesn't need any of that. I extracted the three cloud providers (Deepgram, Claude, ElevenLabs) and the WebSocket pipeline into a single `server/garvis_server.py` -- about 450 lines total.

The key simplification: no tool calling, no VAD (Deepgram handles that server-side), no multi-speaker tracking. Just `audio in → text → response → audio out`. API keys come from a `.env` file.

Server deps are optional (`pip install .[server]`) so the base LED matrix package stays light.

## The client

`apps/garvis.py` follows the same standalone pattern as the sports tracker: terminal submenu for configuration, then a custom main loop with Canvas + Simulator + Sender.

### Audio device selection

USB audio devices like the Blue Snowball and Scarlett 2i2 don't support arbitrary sample rates -- the Snowball only does 48kHz, not the 16kHz that Deepgram expects. The client captures at the device's native rate and resamples in the callback:

```python
dev_info = sd.query_devices(self.input_device)
native_rate = int(dev_info["default_samplerate"])
ratio = native_rate / 16000

def callback(indata, frames, time_info, status):
    samples = indata[:, 0].astype(np.float32)
    n_out = int(len(samples) / ratio)
    resampled = np.interp(
        np.linspace(0, len(samples), n_out, endpoint=False),
        np.arange(len(samples)), samples,
    ).astype(np.int16)
    asyncio.run_coroutine_threadsafe(ws.send(resampled.tobytes()), loop)
```

Same trick on the output side -- resample decoded audio to the speaker's native rate before playing.

One gotcha: sounddevice device indices shift between runs as PulseAudio/PipeWire re-enumerates. The config saves device names (e.g. `"Blue Snowball Mono"`) and resolves to the current index at startup.

### MP3 decoding

ElevenLabs sends MP3 over the WebSocket. The client tries WAV first (for Piper/Kokoro compatibility), then falls back to ffmpeg for MP3:

```python
proc = subprocess.run(
    ["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"],
    input=data, capture_output=True, timeout=5,
)
```

### Threading model

The main thread runs the pygame display loop. A background thread runs an asyncio event loop with the WebSocket client, mic capture, and audio playback. Shared state is a plain dict -- the asyncio thread writes status/captions, the display thread reads them. No locks needed since we're replacing immutable string values.

## The face

Top half of the 64x64 display is a pixel-art face that reacts to state:

- **Connecting**: dim pulsing eyes, orange dot
- **Idle**: closed eyes (horizontal lines) with a blink every ~5 seconds
- **Listening**: open circular eyes with a breathing pulse
- **Speaking**: open eyes + an animated mouth bar that oscillates width

The eyes are drawn at `(18, 13)` and `(38, 13)` -- 20 pixels apart, centered on the 64-wide display. Open eyes use a parametric circle (`r=4`, 32 angle steps) with a 2x2 pixel pupil.

## Captions

Bottom half shows the streamed assistant response, word-wrapped to 15 characters per line (the 3x5 font at 4px per character fits 15 chars in the 60px between 2px margins). Five visible lines auto-scroll to the bottom as text streams in.

## How to use it

```bash
# Terminal 1: start the voice server
./run  # pick g)

# Terminal 2: start the display client
./run  # pick 9), configure audio devices, then s) to start
```

API keys go in `server/.env` or the project root `.env`. See `server/.env.example`.
