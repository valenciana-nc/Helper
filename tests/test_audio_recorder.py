from __future__ import annotations

import io
import wave
from unittest.mock import Mock, patch

import numpy as np
import pytest

import audio_recorder
from audio_recorder import AudioRecorder


def test_start_recording_without_portaudio_raises_clear_error(monkeypatch):
    monkeypatch.setattr(audio_recorder, "sd", None)
    recorder = AudioRecorder()
    with pytest.raises(RuntimeError, match="PortAudio"):
        recorder.start_recording()
    assert not recorder._recording


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


def test_start_recording_failure_closes_stream_without_publishing(monkeypatch):
    stream = Mock()
    stream.start.side_effect = RuntimeError("device busy")
    fake_sd = Mock()
    fake_sd.InputStream.return_value = stream
    monkeypatch.setattr(audio_recorder, "sd", fake_sd)
    recorder = AudioRecorder()
    with pytest.raises(RuntimeError, match="device busy"):
        recorder.start_recording()
    stream.close.assert_called_once()
    assert recorder._stream is None
    assert not recorder._recording


def test_finalize_stream_closes_even_when_stop_raises():
    recorder = AudioRecorder()
    stream = Mock()
    stream.stop.side_effect = RuntimeError("device unplugged")
    recorder._stream = stream
    recorder._recording = True

    recorder.cancel_recording()  # must not propagate: hotkey thread safety

    stream.stop.assert_called_once()
    stream.close.assert_called_once()
    assert recorder._stream is None
    assert not recorder._recording
