"""
Lightweight Garvis voice server for LED matrix client.
Single-file server: Deepgram STT -> LLM -> ElevenLabs TTS

Copied/trimmed from the main Garvis project. No Discord, no MCP, no local models.
Just the WebSocket voice pipeline for a mic+speaker client.

LLM provider (choose one):
    USE_OPENCLAW=true (default) - OpenClaw Gateway (persistent memory, multi-model)
    USE_OPENCLAW=false           - Direct Anthropic Claude API

Requires API keys in .env (or environment):
    DEEPGRAM_API_KEY=...
    ELEVENLABS_API_KEY=...

    # For OpenClaw (default):
    OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
    OPENCLAW_GATEWAY_TOKEN=...

    # For direct Claude:
    ANTHROPIC_API_KEY=...

Usage:
    python server/garvis_server.py
"""

import asyncio
import base64
from difflib import SequenceMatcher
import json
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import AsyncGenerator, Awaitable, Callable, Optional

import aiohttp
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import websockets

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent / ".env")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# LLM provider toggle: OpenClaw (default) or direct Claude
USE_OPENCLAW = os.getenv("USE_OPENCLAW", "true").lower() == "true"

# OpenClaw settings
OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
OPENCLAW_AGENT_ID = os.getenv("OPENCLAW_AGENT_ID", "main")
OPENCLAW_SESSION_KEY = os.getenv("OPENCLAW_SESSION_KEY", "led-matrix-garvis")

# Direct Claude (fallback when USE_OPENCLAW=false)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-2")
DEEPGRAM_ENDPOINTING = int(os.getenv("DEEPGRAM_ENDPOINTING", "500"))
DEEPGRAM_UTTERANCE_END_MS = int(os.getenv("DEEPGRAM_UTTERANCE_END_MS", "1200"))

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022")
CLAUDE_SYSTEM_PROMPT = os.getenv(
    "CLAUDE_SYSTEM_PROMPT",
    "You are Garvis, a voice AI assistant. Keep replies to 1-2 sentences max. Be direct.",
)
MAX_CONVERSATION_TURNS = int(os.getenv("MAX_CONVERSATION_TURNS", "10"))

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
ELEVENLABS_OUTPUT_FORMAT = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")

ASSISTANT_MODE = os.getenv("ASSISTANT_MODE", "true").lower() == "true"
WAKE_WORD = os.getenv("WAKE_WORD", "garvis")

SERVER_HOST = os.getenv("GARVIS_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("GARVIS_PORT", "8000"))


# ---------------------------------------------------------------------------
# Deepgram STT
# ---------------------------------------------------------------------------

class DeepgramSTT:
    """Real-time speech-to-text via Deepgram streaming WebSocket."""

    DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"

    def __init__(
        self,
        on_transcript: Callable[[str, bool], Awaitable[None]],
        on_speech_end: Callable[[str], Awaitable[None]],
    ):
        self.on_transcript = on_transcript
        self.on_speech_end = on_speech_end
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.current_transcript = ""
        self._connected = False
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._speech_final_fired = False
        self._last_audio_time = 0.0

    async def connect(self):
        if not DEEPGRAM_API_KEY:
            raise ValueError("DEEPGRAM_API_KEY is not set")
        params = {
            "model": DEEPGRAM_MODEL,
            "language": "en-US",
            "smart_format": "true",
            "encoding": "linear16",
            "channels": "1",
            "sample_rate": "16000",
            "vad_events": "true",
            "interim_results": "true",
            "utterance_end_ms": str(DEEPGRAM_UTTERANCE_END_MS),
            "endpointing": str(DEEPGRAM_ENDPOINTING),
        }
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.DEEPGRAM_WS_URL}?{query_string}"
        headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(url, headers=headers)
        self._connected = True
        self._speech_final_fired = False
        print("[stt] Deepgram connected")
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def disconnect(self):
        self._connected = False
        for task in (self._keepalive_task, self._receive_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._keepalive_task = self._receive_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    async def send_audio(self, audio_bytes: bytes):
        if self._ws and self._connected:
            try:
                await self._ws.send_bytes(audio_bytes)
                self._last_audio_time = time.time()
            except Exception:
                self._connected = False

    async def _keepalive_loop(self):
        try:
            while self._connected:
                await asyncio.sleep(1.0)
                if not self._connected or not self._ws:
                    break
                if time.time() - self._last_audio_time >= 5.0:
                    try:
                        await self._ws.send_str(json.dumps({"type": "KeepAlive"}))
                        self._last_audio_time = time.time()
                    except Exception:
                        self._connected = False
                        break
        except asyncio.CancelledError:
            pass

    async def _receive_loop(self):
        try:
            async for msg in self._ws:
                if not self._connected:
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        await self._handle_message(json.loads(msg.data))
                    except Exception as e:
                        print(f"[stt] Error: {e}")
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    self._connected = False
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[stt] Receive error: {e}")
            self._connected = False

    async def _handle_message(self, data: dict):
        msg_type = data.get("type", "")
        if msg_type == "Results":
            alts = data.get("channel", {}).get("alternatives", [])
            if alts:
                transcript = alts[0].get("transcript", "")
                is_final = data.get("is_final", False)
                speech_final = data.get("speech_final", False)
                if transcript:
                    if is_final:
                        if self.current_transcript:
                            self.current_transcript += " " + transcript
                        else:
                            self.current_transcript = transcript
                    display = self.current_transcript if is_final else transcript
                    await self.on_transcript(display, is_final)
                if speech_final and not self._speech_final_fired:
                    final = self.current_transcript or transcript
                    if final:
                        self._speech_final_fired = True
                        await self.on_speech_end(final)
                        self.current_transcript = ""
        elif msg_type == "UtteranceEnd":
            if self.current_transcript and not self._speech_final_fired:
                await self.on_speech_end(self.current_transcript)
                self.current_transcript = ""
            self._speech_final_fired = False
        elif msg_type == "SpeechStarted":
            self._speech_final_fired = False


# ---------------------------------------------------------------------------
# LLM (OpenClaw or Claude)
# ---------------------------------------------------------------------------

class OpenClawLLM:
    """OpenClaw Gateway streaming LLM (OpenAI-compatible SSE API)."""

    def __init__(self):
        self.gateway_url = OPENCLAW_GATEWAY_URL.rstrip("/")
        self.token = OPENCLAW_GATEWAY_TOKEN
        self.agent_id = OPENCLAW_AGENT_ID
        self.session_key = OPENCLAW_SESSION_KEY
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0)
        )

    def _get_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def stream_response(
        self, conversation_history: list[dict], max_tokens: int = 1024
    ) -> AsyncGenerator[str, None]:
        if not conversation_history:
            return

        messages = []
        if CLAUDE_SYSTEM_PROMPT:
            messages.append({"role": "system", "content": CLAUDE_SYSTEM_PROMPT})

        max_msgs = MAX_CONVERSATION_TURNS * 2
        recent = conversation_history[-max_msgs:] if len(conversation_history) > max_msgs else conversation_history
        messages.extend(recent)

        payload = {
            "model": self.agent_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "user": self.session_key,
        }

        url = f"{self.gateway_url}/v1/chat/completions"

        try:
            async with self._client.stream(
                "POST", url, headers=self._get_headers(), json=payload
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    print(f"[llm] OpenClaw error {response.status_code}: {error_text.decode()}")
                    yield "Sorry, I encountered an error connecting to OpenClaw."
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                            if choices[0].get("finish_reason") == "stop":
                                break
                    except json.JSONDecodeError:
                        continue

        except httpx.ConnectError as e:
            print(f"[llm] OpenClaw connection error: {e}")
            print(f"[llm] Make sure OpenClaw Gateway is running at {self.gateway_url}")
            yield "Sorry, I cannot connect to OpenClaw. Is the gateway running?"
        except Exception as e:
            print(f"[llm] OpenClaw error: {e}")
            yield "Sorry, I encountered an error."


class ClaudeLLM:
    """Claude streaming LLM (no tool calling). Used when USE_OPENCLAW=false."""

    def __init__(self):
        from anthropic import AsyncAnthropic
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self.client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def stream_response(
        self, conversation_history: list[dict], max_tokens: int = 1024
    ) -> AsyncGenerator[str, None]:
        max_msgs = MAX_CONVERSATION_TURNS * 2
        recent = conversation_history[-max_msgs:] if len(conversation_history) > max_msgs else conversation_history
        try:
            async with self.client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=CLAUDE_SYSTEM_PROMPT,
                messages=recent,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            print(f"[llm] Claude error: {e}")
            yield "Sorry, I encountered an error."


# ---------------------------------------------------------------------------
# ElevenLabs TTS
# ---------------------------------------------------------------------------

class AudioBuffer:
    def __init__(self, prebuffer_bytes: int = 4000):
        self._prebuffer_bytes = prebuffer_bytes
        self._buffer: deque[bytes] = deque()
        self._total_bytes = 0
        self._finished = False

    def add_audio(self, data: bytes):
        if data:
            self._buffer.append(data)
            self._total_bytes += len(data)

    def mark_finished(self):
        self._finished = True

    def is_ready(self) -> bool:
        return self._total_bytes >= self._prebuffer_bytes or self._finished

    def get_all_audio(self) -> Optional[bytes]:
        if not self.is_ready() and not self._finished:
            return None
        if self._total_bytes == 0:
            return None
        all_data = b"".join(self._buffer)
        self._buffer.clear()
        self._total_bytes = 0
        return all_data

    def reset(self):
        self._buffer.clear()
        self._total_bytes = 0
        self._finished = False


class ElevenLabsTTS:
    """ElevenLabs multi-context WebSocket TTS."""

    WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/multi-stream-input"
    MIN_TEXT_CHARS = 50
    MIN_TEXT_CHARS_FIRST = 20
    KEEP_ALIVE_INTERVAL = 15

    def __init__(self, on_audio: Callable[[bytes], Awaitable[None]]):
        if not ELEVENLABS_API_KEY:
            raise ValueError("ELEVENLABS_API_KEY is not set")
        self.on_audio = on_audio
        self._ws = None
        self._connected = False
        self._receive_task: Optional[asyncio.Task] = None
        self._playback_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._current_context_id: Optional[str] = None
        self._is_speaking = False
        self._stop_event = asyncio.Event()
        self._audio_buffer = AudioBuffer(prebuffer_bytes=4000)
        self._text_buffer = ""
        self._is_first_text_chunk = True
        self._completed_contexts: set[str] = set()

    async def connect(self):
        if self._connected and self._ws:
            return
        url = self.WS_URL.format(voice_id=ELEVENLABS_VOICE_ID)
        params = [
            f"model_id={ELEVENLABS_MODEL_ID}",
            f"output_format={ELEVENLABS_OUTPUT_FORMAT}",
            "inactivity_timeout=180",
        ]
        url = f"{url}?{'&'.join(params)}"
        self._ws = await websockets.connect(
            url,
            max_size=16 * 1024 * 1024,
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        self._connected = True
        print("[tts] ElevenLabs connected")
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self):
        try:
            while self._connected and self._ws:
                await asyncio.sleep(self.KEEP_ALIVE_INTERVAL)
                if self._ws and self._connected and self._current_context_id:
                    try:
                        await self._ws.send(json.dumps({
                            "context_id": self._current_context_id,
                            "text": " ",
                        }))
                    except Exception:
                        await self._handle_disconnect()
                        break
        except asyncio.CancelledError:
            pass

    async def _handle_disconnect(self):
        self._connected = False
        self._ws = None
        if self._is_speaking:
            self._audio_buffer.mark_finished()

    async def _receive_loop(self):
        try:
            while self._connected and self._ws:
                try:
                    message = await asyncio.wait_for(self._ws.recv(), timeout=0.5)
                    data = json.loads(message)
                    ctx = data.get("contextId", data.get("context_id"))
                    if ctx in self._completed_contexts:
                        continue
                    if "error" in data:
                        print(f"[tts] ElevenLabs error: {data.get('error')}")
                        continue
                    if ctx == self._current_context_id:
                        if "audio" in data and data["audio"]:
                            self._audio_buffer.add_audio(base64.b64decode(data["audio"]))
                        if data.get("isFinal", False) or data.get("is_final", False):
                            self._audio_buffer.mark_finished()
                            self._completed_contexts.add(ctx)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    await self._handle_disconnect()
                    break
        except asyncio.CancelledError:
            pass

    async def _playback_loop(self):
        try:
            while not self._stop_event.is_set():
                if self._audio_buffer.is_ready():
                    break
                if self._audio_buffer._finished and self._audio_buffer._total_bytes == 0:
                    return
                await asyncio.sleep(0.01)
            while not self._stop_event.is_set():
                audio = self._audio_buffer.get_all_audio()
                if audio:
                    await self.on_audio(audio)
                if self._audio_buffer._finished and self._audio_buffer._total_bytes == 0:
                    break
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass

    async def add_text(self, text: str):
        if not self._connected or not self._ws:
            await self.connect()
        if not self._is_speaking:
            self._is_speaking = True
            self._is_first_text_chunk = True
            self._stop_event.clear()
            self._audio_buffer.reset()
            self._text_buffer = ""
            self._current_context_id = f"ctx_{uuid.uuid4().hex[:8]}"
            init = {
                "context_id": self._current_context_id,
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.0},
                "generation_config": {"chunk_length_schedule": [50, 120, 200, 260]},
            }
            try:
                await self._ws.send(json.dumps(init))
            except Exception:
                await self._handle_disconnect()
                await self.connect()
                await self._ws.send(json.dumps(init))
            self._playback_task = asyncio.create_task(self._playback_loop())
        self._text_buffer += text
        threshold = self.MIN_TEXT_CHARS_FIRST if self._is_first_text_chunk else self.MIN_TEXT_CHARS
        if len(self._text_buffer) >= threshold:
            flush_first = self._is_first_text_chunk
            self._is_first_text_chunk = False
            await self._send_buffered_text(flush=flush_first)

    async def _send_buffered_text(self, flush: bool = False):
        if not self._text_buffer or not self._ws or not self._current_context_id:
            return
        text = self._text_buffer
        self._text_buffer = ""
        try:
            await self._ws.send(json.dumps({
                "context_id": self._current_context_id,
                "text": text,
                "flush": flush,
            }))
        except Exception as e:
            print(f"[tts] Send error: {e}")

    async def flush(self):
        if not self._is_speaking:
            return
        if self._text_buffer:
            await self._send_buffered_text(flush=True)
        if self._ws and self._current_context_id:
            try:
                await self._ws.send(json.dumps({
                    "context_id": self._current_context_id,
                    "close_context": True,
                }))
            except Exception:
                pass
        if self._playback_task:
            try:
                await asyncio.wait_for(self._playback_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if self._playback_task:
                    self._playback_task.cancel()
            self._playback_task = None
        self._is_speaking = False
        self._current_context_id = None

    async def stop(self):
        self._stop_event.set()
        if self._ws and self._current_context_id:
            try:
                await self._ws.send(json.dumps({
                    "context_id": self._current_context_id,
                    "close_context": True,
                }))
                self._completed_contexts.add(self._current_context_id)
            except Exception:
                pass
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            self._playback_task = None
        self._audio_buffer.reset()
        self._text_buffer = ""
        self._is_speaking = False
        self._current_context_id = None
        self._stop_event.clear()

    async def disconnect(self):
        for task in (self._keepalive_task, self._receive_task, self._playback_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._keepalive_task = self._receive_task = self._playback_task = None
        if self._ws:
            try:
                await self._ws.send(json.dumps({"close_socket": True}))
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._connected = False
        self._audio_buffer.reset()
        self._completed_contexts.clear()


# ---------------------------------------------------------------------------
# Voice Pipeline
# ---------------------------------------------------------------------------

class VoicePipeline:
    """STT -> LLM -> TTS pipeline for a single WebSocket client."""

    @staticmethod
    def _normalize(text: str) -> str:
        text = re.sub(r"\bjarvis\b", "Garvis", text, flags=re.IGNORECASE)
        text = re.sub(r"\btravis\b", "Garvis", text, flags=re.IGNORECASE)
        return text

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.stt: Optional[DeepgramSTT] = None
        self.llm: Optional[OpenClawLLM | ClaudeLLM] = None
        self.tts: Optional[ElevenLabsTTS] = None
        self.is_listening = False
        self.is_speaking = False
        self.assistant_mode = ASSISTANT_MODE
        self.conversation_history: list[dict] = []
        self.current_transcript = ""
        self._running = False
        self._speak_end_time = 0.0  # timestamp when speaking ended (for echo suppression)
        self._last_tts_text = ""    # last assistant response text (for textual echo cancellation)
        self._last_tts_time = 0.0   # when that response was generated

    async def start(self):
        self._running = True
        self.stt = DeepgramSTT(
            on_transcript=self._handle_transcript,
            on_speech_end=self._handle_speech_end,
        )
        if USE_OPENCLAW:
            self.llm = OpenClawLLM()
            print("[pipeline] LLM: OpenClaw")
        else:
            self.llm = ClaudeLLM()
            print("[pipeline] LLM: Claude (direct)")
        self.tts = ElevenLabsTTS(on_audio=self._send_audio)
        await self.stt.connect()
        await self.tts.connect()
        await self._send_status()
        print("[pipeline] Ready")

    async def cleanup(self):
        self._running = False
        if self.stt:
            await self.stt.disconnect()
        if self.tts:
            await self.tts.stop()
            await self.tts.disconnect()

    async def process_audio(self, audio_bytes: bytes):
        if not self._running or not self.stt:
            return
        # Don't send mic audio to STT while speaking (prevents echo feedback loop)
        if self.is_speaking:
            return
        # Brief cooldown after speaking to let echo die out before re-enabling mic
        if time.time() - self._speak_end_time < 1.0:
            return
        await self.stt.send_audio(audio_bytes)

    async def handle_control(self, data: dict):
        msg_type = data.get("type")
        if msg_type == "start":
            self.is_listening = True
            await self._send_status()
        elif msg_type == "stop":
            self.is_listening = False
            await self._send_status()
        elif msg_type == "interrupt":
            if self.tts:
                await self.tts.stop()
            self.is_speaking = False
            self._speak_end_time = time.time()
            await self._send_status()
        elif msg_type == "assistant_mode":
            if "enabled" in data:
                self.assistant_mode = bool(data["enabled"])
            else:
                self.assistant_mode = not self.assistant_mode
            print(f"[pipeline] Assistant mode: {'on' if self.assistant_mode else 'off'}")
            await self._send_status()

    async def _handle_transcript(self, text: str, is_final: bool):
        text = self._normalize(text)
        self.current_transcript = text
        await self.ws.send_json({
            "type": "transcript",
            "text": text,
            "is_final": is_final,
            "role": "user",
        })
        if not self.is_listening:
            self.is_listening = True
            await self._send_status()

    def _is_echo(self, transcript: str, threshold: float = 0.5) -> bool:
        """Check if transcript is likely echo of the last TTS output (textual echo cancellation)."""
        if not self._last_tts_text:
            return False
        # Only check within a reasonable time window after speaking
        if time.time() - self._last_tts_time > 15:
            return False
        ratio = SequenceMatcher(
            None, transcript.lower().strip(), self._last_tts_text.lower().strip()
        ).ratio()
        if ratio > threshold:
            print(f"[pipeline] Echo detected (similarity={ratio:.2f}): '{transcript[:50]}'")
            return True
        # Also check if the transcript is a substring of the last response
        if len(transcript.strip()) > 5 and transcript.lower().strip() in self._last_tts_text.lower():
            print(f"[pipeline] Echo detected (substring): '{transcript[:50]}'")
            return True
        return False

    def _check_wake_word(self, transcript: str) -> tuple[bool, str]:
        """Check if transcript starts with the wake word.
        Returns (has_wake_word, cleaned_transcript).
        Handles punctuation from Deepgram smart_format (e.g. 'Hey, Garvis.')."""
        wake = re.escape(WAKE_WORD)
        # Match: optional prefix (hey/hi/ok/okay) + wake word + optional punctuation
        pattern = rf"^(?:(?:hey|hi|ok|okay)[,.]?\s+)?{wake}[,.\s!?]*"
        m = re.match(pattern, transcript, re.IGNORECASE)
        if not m:
            return False, transcript
        cleaned = transcript[m.end():].strip()
        return True, cleaned

    async def _handle_speech_end(self, final_transcript: str):
        if not final_transcript.strip():
            return
        final_transcript = self._normalize(final_transcript)

        # Textual echo cancellation: discard if transcript matches recent TTS output
        if self._is_echo(final_transcript):
            return

        # Assistant mode: only respond when user says the wake word
        if self.assistant_mode:
            has_wake_word, cleaned = self._check_wake_word(final_transcript)
            if not has_wake_word:
                print(f"[pipeline] Assistant mode: ignoring '{final_transcript[:50]}' (no wake word)")
                return
            if cleaned:
                final_transcript = cleaned
                print(f"[pipeline] Wake word detected: '{final_transcript[:50]}'")
            else:
                # User just said "Garvis" with no command — acknowledge
                print("[pipeline] Wake word only — waiting for command")
                return

        self.is_listening = False
        await self._send_status()
        self.conversation_history.append({"role": "user", "content": final_transcript})
        self.is_speaking = True
        await self._send_status()
        assistant_response = ""
        async for chunk in self.llm.stream_response(self.conversation_history):
            assistant_response += chunk
            await self.ws.send_json({
                "type": "transcript",
                "text": assistant_response,
                "is_final": False,
                "role": "assistant",
            })
            await self.tts.add_text(chunk)
        final_response = re.sub(r"\s+", " ", assistant_response).strip()
        if final_response:
            self.conversation_history.append({"role": "assistant", "content": final_response})
            self._last_tts_text = final_response
            self._last_tts_time = time.time()
            await self.ws.send_json({
                "type": "transcript",
                "text": final_response,
                "is_final": True,
                "role": "assistant",
            })
            await self.tts.flush()
        self.is_speaking = False
        self._speak_end_time = time.time()
        await self._send_status()

    async def _send_audio(self, audio_bytes: bytes):
        if self._running:
            try:
                await self.ws.send_bytes(audio_bytes)
            except Exception:
                pass

    async def _send_status(self):
        if self._running:
            try:
                await self.ws.send_json({
                    "type": "status",
                    "listening": self.is_listening,
                    "speaking": self.is_speaking,
                    "assistant_mode": self.assistant_mode,
                })
            except Exception:
                pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Garvis Voice Server (Lightweight)")

pipelines: dict[WebSocket, VoicePipeline] = {}


@app.websocket("/ws/voice")
async def voice_websocket(ws: WebSocket):
    await ws.accept()
    pipeline = VoicePipeline(ws)
    pipelines[ws] = pipeline
    print(f"[ws] Client connected ({len(pipelines)} total)")

    try:
        await pipeline.start()
        while True:
            message = await ws.receive()
            if "bytes" in message:
                await pipeline.process_audio(message["bytes"])
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    await pipeline.handle_control(data)
                except json.JSONDecodeError:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[ws] Error: {e}")
    finally:
        if ws in pipelines:
            p = pipelines.pop(ws)
            asyncio.create_task(p.cleanup())
        print(f"[ws] Client disconnected ({len(pipelines)} total)")


@app.get("/health")
async def health():
    missing = []
    if not DEEPGRAM_API_KEY:
        missing.append("DEEPGRAM_API_KEY")
    if USE_OPENCLAW:
        if not OPENCLAW_GATEWAY_TOKEN:
            missing.append("OPENCLAW_GATEWAY_TOKEN")
    else:
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    if not ELEVENLABS_API_KEY:
        missing.append("ELEVENLABS_API_KEY")
    return {
        "status": "ok" if not missing else "missing_keys",
        "missing_keys": missing,
        "llm": "openclaw" if USE_OPENCLAW else "claude",
        "clients": len(pipelines),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    missing = []
    if not DEEPGRAM_API_KEY:
        missing.append("DEEPGRAM_API_KEY")
    if USE_OPENCLAW:
        if not OPENCLAW_GATEWAY_TOKEN:
            missing.append("OPENCLAW_GATEWAY_TOKEN")
    else:
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    if not ELEVENLABS_API_KEY:
        missing.append("ELEVENLABS_API_KEY")
    if missing:
        print(f"\n  WARNING: Missing keys: {', '.join(missing)}")
        print(f"  Set them in .env or as environment variables.\n")

    llm_label = f"OpenClaw ({OPENCLAW_GATEWAY_URL})" if USE_OPENCLAW else f"Claude ({CLAUDE_MODEL})"
    mode_label = f"on (wake word: \"{WAKE_WORD}\")" if ASSISTANT_MODE else "off"
    print(f"\n  Garvis Voice Server starting on {SERVER_HOST}:{SERVER_PORT}")
    print(f"  LLM provider:       {llm_label}")
    print(f"  Assistant mode:     {mode_label}")
    print(f"  WebSocket endpoint: ws://{SERVER_HOST}:{SERVER_PORT}/ws/voice")
    print(f"  Health check:       http://{SERVER_HOST}:{SERVER_PORT}/health\n")

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, log_level="warning")
