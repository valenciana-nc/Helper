"""Adapters between recorded WAV input and Helper's streaming voice runtime."""
from __future__ import annotations

import wave
from io import BytesIO

import numpy as np


def wav_to_pcm16(wav_bytes: bytes) -> bytes:
    """Decode PCM WAV audio to mono signed 16-bit samples."""
    with wave.open(BytesIO(wav_bytes), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
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

    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


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
