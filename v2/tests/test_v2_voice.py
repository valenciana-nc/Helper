"""Tests for v2/voice.py — streaming VAD/STT/TTS.

No network, no real models, no audio device. sounddevice is never imported
here (voice.py only imports it lazily inside _play_wav, which is
monkeypatched in TTS tests).
"""

import io
import sys
import threading
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v2 import voice as vm


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sine_pcm(freq=440.0, seconds=0.5, amplitude=0.5, rate=16000) -> bytes:
    n = int(rate * seconds)
    t = np.arange(n) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16).tobytes()


def _quiet_pcm(seconds=0.1, rate=16000) -> bytes:
    rng = np.random.RandomState(0)
    return (rng.randn(int(rate * seconds)) * 20).astype(np.int16).tobytes()


def _canned_wav(seconds=0.05) -> bytes:
    return vm.pcm16_to_wav(b"\x01\x00" * int(16000 * seconds))


def _poll(fn, timeout=5.0):
    deadline = __import__("time").monotonic() + timeout
    while __import__("time").monotonic() < deadline:
        out = fn()
        if out is not None:
            return out
        __import__("time").sleep(0.02)
    raise AssertionError("timed out waiting for result")


# ---------------------------------------------------------------------------
# VAD
# ---------------------------------------------------------------------------

def test_energy_vad_speech_vs_silence():
    assert isinstance(vm.EnergyVad(), vm.Vad)
    # Fresh instances: hangover state must not leak between these checks.
    assert vm.EnergyVad(threshold=0.02).is_speech(_sine_pcm()) is True
    assert vm.EnergyVad(threshold=0.02).is_speech(_quiet_pcm()) is False


def test_energy_vad_empty_input():
    vad = vm.EnergyVad()
    assert vad.is_speech(b"") is False


def test_energy_vad_hangover():
    vad = vm.EnergyVad(threshold=0.02, hangover_frames=2)
    assert vad.is_speech(_sine_pcm(seconds=0.1)) is True
    assert vad.is_speech(_quiet_pcm()) is True   # hangover 1
    assert vad.is_speech(_quiet_pcm()) is True   # hangover 2
    assert vad.is_speech(_quiet_pcm()) is False  # expired


def test_silero_vad_missing_torch_raises_with_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(RuntimeError, match="pip install"):
        vm.SileroVad()


# ---------------------------------------------------------------------------
# STT — fake local backend via sys.modules injection
# ---------------------------------------------------------------------------

def _install_fake_faster_whisper(monkeypatch):
    """Inject a stub faster_whisper module; returns the created-model registry."""
    created = []
    lock = threading.Lock()

    class FakeWhisperModel:
        def __init__(self, *a, **k):
            self.script = []
            self._last = ""
            created.append(self)

        def transcribe(self, audio, **k):
            with lock:
                text = self.script.pop(0) if self.script else self._last
                self._last = text
            return ([SimpleNamespace(text=text)], None)

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    return created


def test_stt_partial_diff_with_fake_local(monkeypatch):
    created = _install_fake_faster_whisper(monkeypatch)
    s = vm.SttStreamer(prefer_local=True, partial_every_s=0.05)
    s.start()
    created[0].script = ["hello", "hello world"]

    pcm = b"\x00\x00" * 3200  # 0.2 s of silence-ish PCM
    s.feed(pcm)
    p1 = _poll(lambda: s.feed(b""))
    assert p1 == "hello"

    s.feed(pcm)
    p2 = _poll(lambda: s.feed(b""))
    assert p2 == " world"  # only the new suffix

    assert s.finish() == "hello world"


def test_stt_feed_before_start_raises():
    s = vm.SttStreamer()
    with pytest.raises(RuntimeError, match="start"):
        s.feed(b"\x00\x00")


def test_stt_no_backend_raises_with_instructions(monkeypatch):
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    monkeypatch.setattr(vm, "_openai_api_key", lambda: None)
    s = vm.SttStreamer(prefer_local=True)
    with pytest.raises(RuntimeError, match="pip install faster-whisper"):
        s.start()


def test_stt_cloud_fallback(monkeypatch):
    # Local missing, cloud available via fakes: no partials, finish() works.
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    monkeypatch.setattr(vm, "_openai_api_key", lambda: "fake-key")

    class FakeClient:
        def transcribe(self, wav_bytes, *, model):
            assert wav_bytes[:4] == b"RIFF"
            assert model == "whisper-1"
            return "cloud says hi"

    fake_oc = types.ModuleType("openai_client")
    fake_oc.OpenAIClient = lambda **k: FakeClient()
    monkeypatch.setitem(sys.modules, "openai_client", fake_oc)

    s = vm.SttStreamer(prefer_local=True)
    s.start()
    assert s.feed(b"\x01\x00" * 1600) is None  # cloud: no partials
    assert s.finish() == "cloud says hi"


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

def test_tts_sentence_chunking(monkeypatch):
    t = vm.TtsStreamer()
    synth_calls, play_calls = [], []
    monkeypatch.setattr(
        t, "_synthesize",
        lambda text: (synth_calls.append(text), _canned_wav())[1],
    )
    monkeypatch.setattr(t, "_play_wav", lambda wav: play_calls.append(wav))

    t.speak(["Hello world. ", "How are you", " doing?"])
    t._worker.join(timeout=5)

    assert synth_calls == ["Hello world.", "How are you doing?"]
    assert len(play_calls) == 2


def test_tts_splits_multiple_sentences_in_one_chunk(monkeypatch):
    t = vm.TtsStreamer()
    synth_calls = []
    monkeypatch.setattr(
        t, "_synthesize",
        lambda text: (synth_calls.append(text), _canned_wav())[1],
    )
    monkeypatch.setattr(t, "_play_wav", lambda wav: None)

    t.speak(["One. Two. Three."])
    t._worker.join(timeout=5)

    assert synth_calls == ["One.", "Two.", "Three."]


def test_tts_stop_mid_playback(monkeypatch):
    t = vm.TtsStreamer()
    play_calls = []
    monkeypatch.setattr(t, "_synthesize", lambda text: _canned_wav())

    def fake_play(wav):
        play_calls.append(wav)
        t.stop()  # barge-in during the first sentence

    monkeypatch.setattr(t, "_play_wav", fake_play)
    t.speak(["One. Two. Three."])
    t._worker.join(timeout=5)

    assert len(play_calls) == 1


def test_tts_no_playback_without_sounddevice(monkeypatch, tmp_path):
    # sounddevice missing → WAV dumped to temp dir, playback no-ops with a log.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "sounddevice":
            raise ImportError("no sounddevice")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    t = vm.TtsStreamer(wav_dir=tmp_path)
    t._play_wav(_canned_wav())
    assert list(tmp_path.glob("tts-*.wav")) != []


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def test_pcm16_to_wav_roundtrip():
    pcm = _sine_pcm(seconds=0.1)
    wav = vm.pcm16_to_wav(pcm)
    assert wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == len(pcm) // 2
