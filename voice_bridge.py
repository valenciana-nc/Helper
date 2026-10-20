"""Adapters between recorded WAV input and Helper's streaming voice runtime."""
from __future__ import annotations

import wave
from io import BytesIO

import numpy as np


def wav_to_pcm16(wav_bytes: bytes, *, sample_rate: int = 16000) -> bytes:
    """Decode PCM WAV audio to mono signed 16-bit samples at `sample_rate` Hz.

    The voice runtime's audio currency is 16 kHz, but the recorder runs at
    HELPER_AUDIO_SAMPLE_RATE (env-configurable 8k-192k): without resampling
    here, a non-16k recording reaches STT at the wrong speed and
    transcribes as gibberish. 16 kHz input stays bit-exact.
    """
    with wave.open(BytesIO(wav_bytes), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        src_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128) << 8
    elif sample_width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.int32)
    elif sample_width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        samples = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        samples = (samples ^ 0x800000) - 0x800000
        samples >>= 8
    elif sample_width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.int64) >> 16
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        if samples.size % channels:
            raise ValueError("WAV contains an incomplete audio frame")
        samples = samples.reshape(-1, channels).astype(np.int64).mean(axis=1)

    if src_rate != sample_rate and samples.size:
        samples = _resample_int16(
            np.asarray(samples, dtype=np.float64), src_rate, sample_rate
        )

    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def _resample_int16(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Linear-interpolation resample of mono float audio → int16.

    numpy-only (no scipy): good enough for STT input, and degenerate rates
    can never produce an oversized array or a division by zero.
    """
    if from_rate <= 0 or to_rate <= 0 or len(audio) == 0:
        return np.zeros(0, dtype=np.int16)
    n_out = int(round(len(audio) * to_rate / from_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.int16)
    positions = np.linspace(0.0, len(audio) - 1, n_out)
    resampled = np.interp(positions, np.arange(len(audio), dtype=np.float64), audio)
    return np.clip(resampled, -32768, 32767).astype(np.int16)


def transcribe_audio(wav_bytes: bytes, *, prefer_local: bool = True) -> str:
    """Transcribe recorded audio through the local-first streaming voice runtime."""
    from helper_voice import SttStreamer

    pcm = wav_to_pcm16(wav_bytes)
    stt = SttStreamer(prefer_local=prefer_local)
    stt.start()
    chunk_bytes = 32_000  # one second at 16 kHz, mono, signed 16-bit
    for start in range(0, len(pcm), chunk_bytes):
        stt.feed(pcm[start : start + chunk_bytes])
    return stt.finish().strip()
