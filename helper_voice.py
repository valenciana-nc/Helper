"""Streaming voice runtime: VAD, streaming STT, streaming TTS.

Design notes
------------
* No Qt anywhere in this module.
* All heavy imports are lazy (function-level): ``torch`` (Silero VAD),
  ``faster_whisper`` (local STT), ``sounddevice`` (playback) and the configured
  OpenAI voice client (cloud STT/TTS fallback). Importing this module never
  crashes and never pulls in heavy dependencies.
* Audio currency is 16 kHz mono int16 PCM bytes (``pcm16_16k``).
* Threading: ``SttStreamer.feed`` is safe to call from an audio-callback
  thread; partial transcriptions run on a background worker. ``TtsStreamer``
  runs synthesis/playback on a worker thread; ``stop()`` is thread-safe
  barge-in via a ``threading.Event``.

Test seams
----------
* ``SttStreamer`` resolves its local backend with ``from faster_whisper
  import WhisperModel`` inside ``_LocalWhisperBackend.__init__`` — tests can
  inject a fake by putting a stub module in ``sys.modules["faster_whisper"]``.
* ``TtsStreamer._synthesize`` and ``TtsStreamer._play_wav`` are instance
  methods designed to be monkeypatched in tests.
"""

from __future__ import annotations

import io
import logging
import os
import queue
import re
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
_PCM_BYTES_PER_SEC = SAMPLE_RATE * 2  # 16-bit mono

__all__ = [
    "SAMPLE_RATE",
    "Vad",
    "EnergyVad",
    "SileroVad",
    "SttStreamer",
    "TtsStreamer",
    "pcm16_to_wav",
]


# ---------------------------------------------------------------------------
# WAV helpers (stdlib only)
# ---------------------------------------------------------------------------

def pcm16_to_wav(pcm: bytes, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# VAD
# ---------------------------------------------------------------------------

@runtime_checkable
class Vad(Protocol):
    def is_speech(self, pcm16_16k: bytes) -> bool:
        """Return True if the chunk contains speech. Chunk length is arbitrary."""
        ...


class EnergyVad:
    """Amplitude-threshold VAD over 16 kHz int16 PCM. Pure numpy.

    Always available — this is the default fallback when torch/Silero is
    missing. ``hangover_frames`` keeps reporting speech for a few quiet
    frames after speech ends so word tails aren't chopped.
    """

    def __init__(self, *, threshold: float = 0.02, hangover_frames: int = 4) -> None:
        self.threshold = threshold
        self._hangover_frames = max(0, hangover_frames)
        self._hangover_remaining = 0

    def is_speech(self, pcm16_16k: bytes) -> bool:
        import numpy as np

        if not pcm16_16k:
            return False
        samples = np.frombuffer(pcm16_16k, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return False
        rms = float(np.sqrt(np.mean(samples * samples)))
        if rms >= self.threshold:
            self._hangover_remaining = self._hangover_frames
            return True
        if self._hangover_remaining > 0:
            self._hangover_remaining -= 1
            return True
        return False


class SileroVad:
    """Silero VAD (snakers4/silero-vad) via torch. Lazy import.

    Raises RuntimeError with a pip-install hint when torch/silero is missing
    — never crashes at module import time.
    """

    _WINDOW_SAMPLES = 512  # silero expects 512-sample windows at 16 kHz

    def __init__(self, *, threshold: float = 0.5) -> None:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "SileroVad needs torch and the silero-vad weights: "
                "pip install torch silero-vad"
            ) from exc
        try:
            model, _utils = torch.hub.load(
                "snakers4/silero-vad", "silero_vad", trust_repo=True
            )
        except Exception as exc:
            raise RuntimeError(
                "SileroVad could not load the silero-vad model via torch.hub "
                "(network may be required on first use): "
                "pip install torch silero-vad"
            ) from exc
        self._torch = torch
        self._model = model
        self._threshold = threshold
        self._buf = bytearray()

    def is_speech(self, pcm16_16k: bytes) -> bool:
        window_bytes = self._WINDOW_SAMPLES * 2
        self._buf.extend(pcm16_16k)
        found = False
        while len(self._buf) >= window_bytes:
            window = bytes(self._buf[:window_bytes])
            del self._buf[:window_bytes]
            chunk = self._torch.frombuffer(
                bytearray(window), dtype=self._torch.int16
            ).to(self._torch.float32) / 32768.0
            prob = float(self._model(chunk.unsqueeze(0), SAMPLE_RATE).item())
            if prob >= self._threshold:
                found = True
        return found


# ---------------------------------------------------------------------------
# Cloud helpers (lazy)
# ---------------------------------------------------------------------------

def _openai_api_key() -> str | None:
    """Best-effort API key lookup without importing heavy modules at top level."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    try:
        from config import OPENAI_API_KEY  # Loads .env
    except Exception:
        return None
    return (OPENAI_API_KEY or "").strip() or None


def _openai_voice_client(api_key: str):
    """Lazily build the OpenAI client used by cloud audio APIs."""
    try:
        from openai_client import OpenAIClient
    except ImportError as exc:
        raise RuntimeError(
            "Cloud audio needs Helper's openai_client module on sys.path "
            "(run from the Helper repository root)."
        ) from exc
    return OpenAIClient(optional_api_key=api_key)


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------

class _LocalWhisperBackend:
    """faster-whisper transcription. Raises RuntimeError with install hint."""

    def __init__(self, model_size: str = "base") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Local STT needs faster-whisper: pip install faster-whisper"
            ) from exc
        # CPU int8 is the portable default; GPU users can subclass/override.
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, pcm: bytes) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(audio, beam_size=1)
        return "".join(seg.text for seg in segments).strip()


class _CloudWhisperBackend:
    """whisper-1 via the configured client. No partials — single-shot transcribe."""

    def __init__(self, client) -> None:
        self._client = client

    def transcribe(self, pcm: bytes) -> str:
        wav = pcm16_to_wav(pcm)
        return self._client.transcribe(wav, model="whisper-1")


class SttStreamer:
    """Streaming speech-to-text. Local-first, cloud fallback.

    Usage::

        stt = SttStreamer(prefer_local=True)
        stt.start()
        while recording:
            partial = stt.feed(mic_chunk)   # new partial text, or None
            ...
        final = stt.finish()

    ``feed`` returns only *new* partial text (diffed against what was already
    emitted). With the cloud backend there are no partials: ``feed`` returns
    None and ``finish`` does the single transcription.
    """

    def __init__(
        self,
        *,
        prefer_local: bool = True,
        model_size: str = "base",
        partial_every_s: float = 1.5,
    ) -> None:
        self._prefer_local = prefer_local
        self._model_size = model_size
        self._partial_every_s = max(0.05, partial_every_s)
        self._lock = threading.Lock()
        self._audio = bytearray()
        self._emitted = ""
        self._pending: queue.Queue[str] = queue.Queue()
        self._inflight = False
        self._partial_ok = False  # last scheduled partial transcribed cleanly
        self._last_partial_len = 0
        self._backend = None
        self._is_local = False
        self._started = False

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        backend, is_local = self._select_backend()
        if backend is None:
            raise RuntimeError(
                "Streaming STT unavailable: faster-whisper is not installed "
                "and no cloud fallback is configured. "
                "Install local STT with: pip install faster-whisper "
                "— or set OPENAI_API_KEY for the cloud whisper-1 fallback."
            )
        self._backend = backend
        self._is_local = is_local
        self._started = True
        log.info("SttStreamer started (backend=%s)", "local" if is_local else "cloud")

    def _select_backend(self):
        order = ("local", "cloud") if self._prefer_local else ("cloud", "local")
        first_err: Exception | None = None
        for kind in order:
            try:
                if kind == "local":
                    return _LocalWhisperBackend(self._model_size), True
                cloud = self._try_cloud()
                if cloud is not None:
                    return cloud, False
            except Exception as exc:
                # Broader than RuntimeError on purpose: a faster-whisper model
                # download/load failure (OSError, huggingface_hub HTTP error)
                # is exactly the offline case where the cloud fallback should
                # kick in, not crash start().
                if first_err is None:
                    first_err = exc
                log.warning("%s STT backend unavailable: %s", kind, exc)
        if first_err is not None:
            log.warning("STT backend errors: %s", first_err)
        return None, False

    def _try_cloud(self):
        key = _openai_api_key()
        if not key:
            return None
        try:
            return _CloudWhisperBackend(_openai_voice_client(key))
        except RuntimeError as exc:
            log.warning("Cloud STT unavailable: %s", exc)
            return None

    def _require_started(self) -> None:
        if not self._started or self._backend is None:
            raise RuntimeError("SttStreamer.start() must be called first")

    # -- streaming ------------------------------------------------------

    def feed(self, pcm16_16k: bytes) -> str | None:
        """Feed PCM; return newly-available partial text, or None."""
        self._require_started()
        deltas: list[str] = []
        with self._lock:
            if pcm16_16k:
                self._audio.extend(pcm16_16k)
            # Drain completed background partials.
            while True:
                try:
                    full = self._pending.get_nowait()
                except queue.Empty:
                    break
                self._inflight = False
                delta = self._diff_locked(full)
                if delta:
                    deltas.append(delta)
            # Schedule the next partial (local backend only).
            if (
                self._is_local
                and not self._inflight
                and self._untranscribed_s_locked() >= self._partial_every_s
            ):
                self._inflight = True
                snapshot = bytes(self._audio)
                self._last_partial_len = len(snapshot)
                worker = threading.Thread(
                    target=self._transcribe_partial,
                    args=(snapshot,),
                    daemon=True,
                )
                worker.start()
        if not deltas:
            return None
        return " ".join(deltas)

    def finish(self) -> str:
        """Block until the final transcript for all fed audio is ready."""
        self._require_started()
        # Wait for any in-flight partial, then do one final full
        # transcription — unless the last partial already transcribed every
        # fed byte cleanly, in which case the final pass would run the
        # model over byte-identical input a second time.
        deadline = time.monotonic() + 60
        while self._inflight and time.monotonic() < deadline:
            try:
                full = self._pending.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                self._inflight = False
                self._diff_locked(full)
        with self._lock:
            audio = bytes(self._audio)
            redundant = (
                self._is_local
                and not self._inflight
                and self._partial_ok
                and audio
                and self._last_partial_len == len(audio)
            )
        if audio and not redundant:
            try:
                full = self._backend.transcribe(audio)
            except Exception:
                log.exception("Final STT transcription failed")
                full = ""
            with self._lock:
                self._diff_locked(full)
        with self._lock:
            return self._emitted

    # -- internals ------------------------------------------------------

    def _transcribe_partial(self, snapshot: bytes) -> None:
        try:
            full = self._backend.transcribe(snapshot)
        except Exception:
            log.exception("Partial STT transcription failed")
            full = ""
            self._partial_ok = False
        else:
            self._partial_ok = True
        # Written before put(); the queue's lock makes it visible to the
        # consumer that drains this result.
        self._pending.put(full)

    def _untranscribed_s_locked(self) -> float:
        return (len(self._audio) - self._last_partial_len) / _PCM_BYTES_PER_SEC

    def _diff_locked(self, full: str) -> str | None:
        """Return the suffix of ``full`` not yet emitted (None if nothing new)."""
        full = (full or "").strip()
        if not full:
            return None
        if full.startswith(self._emitted):
            delta = full[len(self._emitted):]
            self._emitted = full
            return delta if delta.strip() else None
        # The model revised earlier words: emit the corrected full text.
        self._emitted = full
        return full


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _split_sentences(buf: str) -> tuple[list[str], str]:
    """Split complete sentences off ``buf``; return (sentences, remainder)."""
    parts = _SENTENCE_END.split(buf)
    if len(parts) <= 1:
        return [], buf
    *complete, rest = parts
    return [p.strip() for p in complete if p.strip()], rest


class TtsStreamer:
    """Streaming text-to-speech with barge-in.

    ``speak`` accepts an iterable of text chunks, synthesizes
    sentence-by-sentence on a worker thread, and plays each sentence as soon
    as it is ready — the first audio does not wait for the full text.
    ``stop()`` halts playback immediately (thread-safe).
    """

    def __init__(
        self,
        *,
        voice: str = "alloy",
        model: str = "gpt-4o-mini-tts",
        wav_dir: str | Path | None = None,
    ) -> None:
        self._voice = voice
        self._model = model
        self._wav_dir = Path(wav_dir) if wav_dir else Path(tempfile.gettempdir()) / "helper-tts"
        self._stop = threading.Event()
        self._worker_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._cloud_client = None

    # -- public --------------------------------------------------------

    def speak(self, text_stream: Iterable[str]) -> None:
        """Start speaking chunks from ``text_stream``. Returns immediately."""
        with self._worker_lock:
            self._stop.set()  # halt any in-progress speech
            old, self._worker = self._worker, None
            # Reset for the new utterance; a stop() during the join below will
            # set it again, which we detect to honor the barge-in.
            self._stop.clear()
        if old is not None:
            old.join(timeout=5)
        with self._worker_lock:
            # A concurrent stop() or speak() during the join set _stop or
            # installed a worker; do not start over it (losing the barge-in).
            if self._stop.is_set() or self._worker is not None:
                return
            worker = threading.Thread(target=self._run, args=(text_stream,), daemon=True)
            self._worker = worker
        worker.start()

    def stop(self) -> None:
        """Barge-in: halt synthesis queuing and stop playback immediately."""
        self._stop.set()
        try:
            import sounddevice as sd
        except (ImportError, OSError):
            # OSError: PortAudio system library missing (not just the
            # Python package) — same graceful degradation.
            return
        try:
            sd.stop()
        except Exception:
            pass

    # -- pipeline -------------------------------------------------------

    def _run(self, text_stream: Iterable[str]) -> None:
        buf = ""
        try:
            for chunk in text_stream:
                if self._stop.is_set():
                    break
                buf += chunk
                sentences, buf = _split_sentences(buf)
                for sentence in sentences:
                    if self._stop.is_set():
                        break
                    self._speak_sentence(sentence)
            tail = buf.strip()
            if tail and not self._stop.is_set():
                self._speak_sentence(tail)
        except Exception:
            log.exception("TTS worker failed")

    def _speak_sentence(self, text: str) -> None:
        if self._stop.is_set() or not text:
            return
        try:
            wav = self._synthesize(text)
        except Exception:
            log.exception("TTS synthesis failed")
            return
        if self._stop.is_set() or not wav:
            return
        try:
            self._play_wav(wav)
        except Exception:
            log.exception("TTS playback failed")

    # -- synthesis ------------------------------------------------------

    def _synthesize(self, text: str) -> bytes:
        """Synthesize one sentence to WAV bytes. Monkeypatchable in tests."""
        local = self._local_synthesize(text)
        if local:
            return local
        return self._cloud_synthesize(text)

    def _local_synthesize(self, text: str) -> bytes | None:
        """Override point for a local TTS engine (kokoro/onnx). None → cloud."""
        return None

    def _cloud_synthesize(self, text: str) -> bytes:
        client = self._ensure_cloud_client()
        wav, _mime = client.synthesize(text, model=self._model, voice=self._voice)
        return wav

    def _ensure_cloud_client(self):
        if self._cloud_client is not None:
            return self._cloud_client
        key = _openai_api_key()
        if not key:
            raise RuntimeError(
                "Cloud TTS needs OPENAI_API_KEY set, or install a local TTS "
                "engine and override TtsStreamer._local_synthesize."
            )
        self._cloud_client = _openai_voice_client(key)
        return self._cloud_client

    # -- playback -------------------------------------------------------

    def _play_wav(self, wav: bytes) -> None:
        """Play WAV bytes via sounddevice; without it, dump to temp dir + log."""
        try:
            import sounddevice as sd
        except (ImportError, OSError):
            # OSError: PortAudio system library missing — dump instead.
            path = self._dump_wav(wav)
            log.info("sounddevice not installed; wrote TTS audio to %s (no playback)", path)
            return
        data, sample_rate = _wav_to_float32(wav)
        if data.size == 0 or self._stop.is_set():
            return
        sd.play(data, sample_rate)
        deadline = time.monotonic() + len(data) / sample_rate + 0.5
        while time.monotonic() < deadline:
            if self._stop.is_set():
                try:
                    sd.stop()
                except Exception:
                    pass
                break
            time.sleep(0.05)

    def _dump_wav(self, wav: bytes) -> Path:
        self._wav_dir.mkdir(parents=True, exist_ok=True)
        # Best-effort unique name without extra imports.
        stamp = f"{time.time_ns()}"
        path = self._wav_dir / f"tts-{stamp}.wav"
        path.write_bytes(wav)
        return path


def _wav_to_float32(wav: bytes):
    """Decode WAV bytes to (float32 mono numpy array, sample rate)."""
    import numpy as np

    with wave.open(io.BytesIO(wav), "rb") as w:
        n_channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        frames = w.readframes(w.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[sampwidth]
    arr = np.frombuffer(frames, dtype=dtype)
    if n_channels > 1:
        arr = arr.reshape(-1, n_channels).mean(axis=1)
    scale = float(2 ** (8 * sampwidth - 1))
    return arr.astype(np.float32) / scale, framerate
