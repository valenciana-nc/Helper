"""Microphone capture for Helper's voice-input pipeline."""
from __future__ import annotations

import io
import logging
import threading
import wave
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

try:
    import sounddevice as sd
except (ImportError, OSError):
    # PortAudio may be missing on headless/CI Linux machines. Audio capture is
    # then unavailable, but the module must still import so the rest of the
    # app (and the test suite) can load; start_recording() raises a clear
    # error instead of a bare OSError at import time.
    sd = None

from config import (
    AUDIO_BLOCKSIZE,
    AUDIO_CHANNELS,
    AUDIO_MIN_SECONDS,
    AUDIO_SAMPLE_RATE,
    AUDIO_SILENCE_THRESHOLD,
    AUDIO_TRIM_PAD_MS,
)


@dataclass(frozen=True)
class RecordedAudio:
    wav_bytes: bytes
    sample_rate: int
    channels: int
    duration_sec: float


class AudioRecorder:
    """Capture microphone input and return a trimmed PCM WAV recording."""

    def __init__(
        self,
        *,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        channels: int = AUDIO_CHANNELS,
        blocksize: int = AUDIO_BLOCKSIZE,
        silence_threshold: int = AUDIO_SILENCE_THRESHOLD,
        min_seconds: float = AUDIO_MIN_SECONDS,
        trim_pad_ms: int = AUDIO_TRIM_PAD_MS,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._silence_threshold = silence_threshold
        self._min_seconds = min_seconds
        self._trim_pad_ms = trim_pad_ms
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._recording = False

    def start_recording(self) -> None:
        if sd is None:
            raise RuntimeError(
                "Audio capture is unavailable: PortAudio is not installed."
            )
        with self._lock:
            if self._recording:
                return
            self._chunks = []
            stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                blocksize=self._blocksize,
                callback=self._on_audio,
            )
            try:
                stream.start()
            except Exception:
                # A half-started stream must never be published to self._stream:
                # the next cancel/stop would call stop() on a stream that never
                # started, and the native PortAudio handle would leak.
                stream.close()
                raise
            self._stream = stream
            self._recording = True

    def cancel_recording(self) -> None:
        self._finalize_stream()
        with self._lock:
            self._chunks = []

    def stop_recording(self) -> RecordedAudio:
        self._finalize_stream()
        with self._lock:
            if not self._chunks:
                raise RuntimeError("No audio was captured.")
            audio = np.concatenate(self._chunks, axis=0)
            self._chunks = []

        trimmed = self._trim_silence(audio)
        duration_sec = len(trimmed) / self._sample_rate
        if duration_sec < self._min_seconds:
            raise RuntimeError("Recording was too short after trimming silence.")
        pcm = np.ascontiguousarray(trimmed.astype(np.int16))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(self._channels)
            wav.setsampwidth(2)
            wav.setframerate(self._sample_rate)
            wav.writeframes(pcm.tobytes())
        return RecordedAudio(output.getvalue(), self._sample_rate, self._channels, duration_sec)

    def _on_audio(self, indata: np.ndarray, frames, time_info, status) -> None:
        del frames, time_info, status
        with self._lock:
            if self._recording:
                self._chunks.append(indata.copy())

    def _trim_silence(self, audio: np.ndarray) -> np.ndarray:
        magnitude = np.abs(audio.astype(np.int32))
        if magnitude.ndim > 1:
            magnitude = magnitude.max(axis=1)
        above_threshold = np.flatnonzero(magnitude >= self._silence_threshold)
        if above_threshold.size == 0:
            return audio
        pad = int(self._sample_rate * self._trim_pad_ms / 1000)
        start = max(int(above_threshold[0]) - pad, 0)
        end = min(int(above_threshold[-1]) + pad + 1, len(audio))
        return audio[start:end]

    def _finalize_stream(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._recording = False
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                # A failing stop (e.g. device unplugged mid-recording) must not
                # skip close(), and must not propagate: cancel_recording() runs
                # on the pynput hotkey thread, where an exception kills the
                # listener and the hotkey goes dead for the rest of the session.
                log.exception("Stopping the audio stream failed; closing it anyway")
            finally:
                stream.close()
