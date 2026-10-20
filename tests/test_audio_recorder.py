from __future__ import annotations

import io
import wave
from unittest.mock import Mock, patch

import numpy as np
import pytest

from audio_recorder import AudioRecorder


def test_recorder_trims_silence_and_returns_wav():
    recorder = AudioRecorder(min_seconds=0.0001, silence_threshold=100, trim_pad_ms=0)
    samples = np.array([0, 0, 0, 500, -500, 0, 0], dtype=np.int16)
    recorder._chunks = [samples]
    with patch.object(recorder, "_finalize_stream"):
        recording = recorder.stop_recording()

    with wave.open(io.BytesIO(recording.wav_bytes), "rb") as result:
        assert result.getframerate() == recording.sample_rate == 16_000
        assert result.getnchannels() == recording.channels == 1
        assert result.readframes(result.getnframes()) == samples[3:5].tobytes()
    assert recording.duration_sec == pytest.approx(2 / 16_000)


def test_cancel_closes_stream_and_discards_captured_chunks():
    recorder = AudioRecorder()
    stream = Mock()
    recorder._stream = stream
    recorder._chunks = [np.ones(8, dtype=np.int16)]
    recorder._recording = True

    recorder.cancel_recording()

    stream.stop.assert_called_once()
    stream.close.assert_called_once()
    assert recorder._stream is None
    assert recorder._chunks == []
    assert not recorder._recording


def test_audio_callback_only_keeps_frames_while_recording():
    recorder = AudioRecorder()
    samples = np.ones((4, 1), dtype=np.int16)
    recorder._on_audio(samples, 4, None, None)
    assert recorder._chunks == []

    recorder._recording = True
    recorder._on_audio(samples, 4, None, None)
    samples[:] = 0
    assert np.all(recorder._chunks[0] == 1)
