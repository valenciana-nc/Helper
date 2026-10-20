from __future__ import annotations

import io
import struct
import wave

import numpy as np
import pytest

from voice_bridge import _resample_int16, wav_to_pcm16


def _wav(pcm: bytes, *, channels: int = 1, sample_width: int = 2, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(sample_width)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return buffer.getvalue()


def test_wav_to_pcm16_keeps_mono_sixteen_bit_samples():
    pcm = struct.pack("<4h", 100, -100, 1000, -1000)
    assert wav_to_pcm16(_wav(pcm)) == pcm


def test_wav_to_pcm16_downmixes_stereo():
    stereo = struct.pack("<4h", 100, 200, 300, 400)
    assert struct.unpack("<2h", wav_to_pcm16(_wav(stereo, channels=2))) == (150, 350)


def test_wav_to_pcm16_converts_unsigned_eight_bit_samples():
    assert struct.unpack("<3h", wav_to_pcm16(_wav(bytes([0, 128, 255]), sample_width=1))) == (
        -32768, 0, 32512
    )


def test_wav_to_pcm16_converts_packed_signed_twenty_four_bit_samples():
    samples = (-8_388_608, 0, 8_388_607)
    packed = b"".join((sample & 0xFFFFFF).to_bytes(3, "little") for sample in samples)
    assert struct.unpack("<3h", wav_to_pcm16(_wav(packed, sample_width=3))) == (-32768, 0, 32767)


def test_wav_to_pcm16_converts_signed_thirty_two_bit_samples():
    pcm = struct.pack("<3i", -2_147_483_648, 0, 2_147_483_647)
    assert struct.unpack("<3h", wav_to_pcm16(_wav(pcm, sample_width=4))) == (-32768, 0, 32767)


def test_wav_to_pcm16_rejects_invalid_wav_data():
    with pytest.raises(wave.Error):
        wav_to_pcm16(b"not a wav")


def test_wav_to_pcm16_leaves_sixteen_kilohertz_audio_bit_exact():
    pcm = struct.pack("<4h", 100, -100, 1000, -1000)
    assert wav_to_pcm16(_wav(pcm, rate=16_000)) == pcm


def test_wav_to_pcm16_resamples_non_sixteen_kilohertz_to_16k():
    # The voice runtime consumes 16 kHz; the recorder can produce 8k-192k.
    src = np.linspace(-1000, 1000, 80).astype("<i2")
    out = wav_to_pcm16(_wav(src.tobytes(), rate=8_000))
    assert len(out) // 2 == 160  # 80 samples at 8kHz -> 160 at 16kHz
    vals = struct.unpack("<160h", out)
    assert vals[0] == pytest.approx(-1000, abs=2)
    assert vals[-1] == pytest.approx(1000, abs=2)


def test_wav_to_pcm16_resample_of_empty_audio_is_empty():
    assert wav_to_pcm16(_wav(b"", rate=8_000)) == b""


def test_resample_int16_rejects_degenerate_rates():
    audio = np.ones(10, dtype=np.float64)
    assert _resample_int16(audio, 0, 16_000).size == 0
    assert _resample_int16(audio, 8_000, 0).size == 0
    assert _resample_int16(np.zeros(0, dtype=np.float64), 8_000, 16_000).size == 0
