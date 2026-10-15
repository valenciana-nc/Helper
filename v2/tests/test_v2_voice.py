"""Tests for v2/voice.py — streaming VAD/STT/TTS.

No network, no real models, no audio device. sounddevice is never imported
here (voice.py only imports it lazily inside _play_wav, which is
monkeypatched in TTS tests).
"""

import io
import struct
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
# STT — diffing, failure paths, backend selection helpers
# ---------------------------------------------------------------------------

def test_stt_diff_locked_empty_and_revision():
    s = vm.SttStreamer()
    assert s._diff_locked("") is None
    assert s._diff_locked("hello") == "hello"
    assert s._diff_locked("hello world") == " world"  # suffix only
    # The model revised earlier words: emit the corrected full text.
    s._emitted = "hello world"
    assert s._diff_locked("hello there") == "hello there"
    assert s._emitted == "hello there"


def test_stt_start_is_idempotent(monkeypatch):
    created = _install_fake_faster_whisper(monkeypatch)
    s = vm.SttStreamer(prefer_local=True)
    s.start()
    s.start()
    assert len(created) == 1
    assert s._started is True


def test_stt_finish_final_transcription_failure(monkeypatch):
    # Backend raises on the final transcribe → finish() swallows it and
    # returns whatever was emitted so far (here: nothing).
    created = _install_fake_faster_whisper(monkeypatch)
    s = vm.SttStreamer(prefer_local=True)
    s.start()

    def boom(audio, **k):
        raise RuntimeError("model exploded")

    created[0].transcribe = boom
    s.feed(b"\x00\x00" * 3200)
    assert s.finish() == ""


def test_stt_partial_transcription_failure_puts_empty(monkeypatch):
    created = _install_fake_faster_whisper(monkeypatch)
    s = vm.SttStreamer(prefer_local=True)
    s.start()

    def boom(audio, **k):
        raise RuntimeError("model exploded")

    created[0].transcribe = boom
    s._transcribe_partial(b"\x00\x00" * 3200)
    assert s._pending.get(timeout=1) == ""


def test_openai_api_key_env_and_config(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "  env-key  ")
    assert vm._openai_api_key() == "env-key"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setitem(
        sys.modules, "config", SimpleNamespace(OPENAI_API_KEY=" cfg-key ")
    )
    assert vm._openai_api_key() == "cfg-key"
    monkeypatch.setitem(sys.modules, "config", None)  # import fails
    assert vm._openai_api_key() is None


def test_v1_openai_client_missing_module_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai_client", None)
    with pytest.raises(RuntimeError, match="openai_client"):
        vm._v1_openai_client("k")


def test_try_cloud_returns_none_when_client_build_fails(monkeypatch):
    monkeypatch.setattr(vm, "_openai_api_key", lambda: "k")

    def boom(key):
        raise RuntimeError("no client")

    monkeypatch.setattr(vm, "_v1_openai_client", boom)
    assert vm.SttStreamer()._try_cloud() is None


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
# TTS — worker, synthesis, playback error paths
# ---------------------------------------------------------------------------

def test_tts_worker_stops_before_first_chunk(monkeypatch):
    t = vm.TtsStreamer()
    synth_calls = []
    monkeypatch.setattr(t, "_synthesize",
                        lambda text: synth_calls.append(text))
    t._stop.set()
    t._run(["Hello."])
    assert synth_calls == []


def test_tts_worker_exception_does_not_propagate(monkeypatch):
    t = vm.TtsStreamer()
    monkeypatch.setattr(t, "_speak_sentence", lambda text: None)

    def bad_stream():
        raise RuntimeError("stream exploded")
        yield "never"

    t._run(bad_stream())  # must not raise


def test_speak_sentence_stop_and_empty(monkeypatch):
    t = vm.TtsStreamer()
    synth_calls = []
    monkeypatch.setattr(t, "_synthesize",
                        lambda text: synth_calls.append(text))
    t._stop.set()
    t._speak_sentence("hi")
    t._stop.clear()
    t._speak_sentence("")
    assert synth_calls == []


def test_speak_sentence_synth_failure_swallowed(monkeypatch):
    t = vm.TtsStreamer()
    monkeypatch.setattr(t, "_synthesize",
                        lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    t._speak_sentence("hi")  # must not raise


def test_speak_sentence_none_synth_skips_playback(monkeypatch):
    t = vm.TtsStreamer()
    monkeypatch.setattr(t, "_synthesize", lambda text: None)
    play_calls = []
    monkeypatch.setattr(t, "_play_wav", lambda wav: play_calls.append(wav))
    t._speak_sentence("hi")
    assert play_calls == []


def test_speak_sentence_playback_failure_swallowed(monkeypatch):
    t = vm.TtsStreamer()
    monkeypatch.setattr(t, "_synthesize", lambda text: _canned_wav())
    monkeypatch.setattr(t, "_play_wav",
                        lambda wav: (_ for _ in ()).throw(RuntimeError("boom")))
    t._speak_sentence("hi")  # must not raise


def test_tts_stop_swallows_sounddevice_stop_error(monkeypatch):
    fake_sd = SimpleNamespace(stop=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    vm.TtsStreamer().stop()  # must not raise


def test_synthesize_prefers_local_then_cloud(monkeypatch):
    t = vm.TtsStreamer()
    monkeypatch.setattr(t, "_local_synthesize", lambda text: _canned_wav())
    monkeypatch.setattr(t, "_cloud_synthesize",
                        lambda text: (_ for _ in ()).throw(AssertionError("cloud must not run")))
    assert t._synthesize("hi") == _canned_wav()
    monkeypatch.setattr(t, "_local_synthesize", lambda text: None)
    monkeypatch.setattr(t, "_cloud_synthesize", lambda text: b"cloud-wav")
    assert t._synthesize("hi") == b"cloud-wav"


def test_local_synthesize_default_is_none():
    assert vm.TtsStreamer()._local_synthesize("hi") is None


def test_cloud_synthesize_uses_client():
    t = vm.TtsStreamer()
    t._cloud_client = SimpleNamespace(
        synthesize=lambda text, *, model, voice: (b"wav-bytes", "audio/wav"))
    assert t._cloud_synthesize("hi") == b"wav-bytes"


def test_ensure_cloud_client_cached_no_key_and_create(monkeypatch):
    t = vm.TtsStreamer()
    sentinel = object()
    t._cloud_client = sentinel
    assert t._ensure_cloud_client() is sentinel
    t._cloud_client = None
    monkeypatch.setattr(vm, "_openai_api_key", lambda: None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        t._ensure_cloud_client()
    fake_client = object()
    monkeypatch.setattr(vm, "_openai_api_key", lambda: "k")
    monkeypatch.setattr(vm, "_v1_openai_client", lambda key: fake_client)
    assert t._ensure_cloud_client() is fake_client
    assert t._cloud_client is fake_client


def _fake_sounddevice():
    played, stopped = [], []

    def play(data, sample_rate):
        played.append((data, sample_rate))

    def stop():
        stopped.append(True)

    return SimpleNamespace(play=play, stop=stop), played, stopped


def test_play_wav_with_fake_sounddevice(monkeypatch):
    fake_sd, played, _ = _fake_sounddevice()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    vm.TtsStreamer()._play_wav(_canned_wav())
    assert len(played) == 1
    assert played[0][1] == 16000


def test_play_wav_empty_audio_skips_playback(monkeypatch):
    fake_sd, played, _ = _fake_sounddevice()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    vm.TtsStreamer()._play_wav(vm.pcm16_to_wav(b""))
    assert played == []


def test_play_wav_stop_during_playback(monkeypatch):
    t = vm.TtsStreamer()
    played, stopped = [], []

    def play(data, sample_rate):
        played.append((data, sample_rate))
        t.stop()  # barge-in from the playback callback

    def stop():
        stopped.append(True)
        raise RuntimeError("stop failed")  # both callers must swallow this

    fake_sd = SimpleNamespace(play=play, stop=stop)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    t._play_wav(_canned_wav())
    assert len(played) == 1
    assert stopped  # wait loop called sd.stop() on barge-in


def test_speak_twice_joins_old_worker(monkeypatch):
    t = vm.TtsStreamer()
    synth_calls = []
    monkeypatch.setattr(
        t, "_synthesize",
        lambda text: (synth_calls.append(text), _canned_wav())[1],
    )
    monkeypatch.setattr(t, "_play_wav", lambda wav: None)
    t.speak(["One."])
    t.speak(["Two."])  # joins the first worker before starting the second
    t._worker.join(timeout=5)
    assert synth_calls == ["One.", "Two."]


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


def _make_wav(frames: bytes, *, sampwidth=2, nchannels=1, framerate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(frames)
    return buf.getvalue()


def test_wav_to_float32_widths_and_stereo():
    # 8-bit: unsigned bytes read as int8, scaled by 2**7.
    arr, rate = vm._wav_to_float32(_make_wav(bytes([0, 128, 255]),
                                             sampwidth=1))
    assert rate == 16000
    assert arr.dtype == np.float32 and len(arr) == 3
    assert arr[0] == 0.0 and arr[1] == -1.0
    # 16-bit mono.
    pcm = struct.pack("<2h", 16384, -16384)
    arr, _ = vm._wav_to_float32(_make_wav(pcm, sampwidth=2))
    assert list(arr) == [0.5, -0.5]
    # 32-bit mono.
    pcm32 = struct.pack("<2i", 2 ** 30, -(2 ** 30))
    arr, _ = vm._wav_to_float32(_make_wav(pcm32, sampwidth=4))
    assert list(arr) == [0.5, -0.5]
    # Stereo is mixed down to mono by averaging channels.
    stereo = struct.pack("<4h", 1000, -1000, 1000, -1000)
    arr, _ = vm._wav_to_float32(_make_wav(stereo, sampwidth=2, nchannels=2))
    assert list(arr) == [0.0, 0.0]


def test_stt_local_load_failure_falls_back_to_cloud(monkeypatch):
    # faster-whisper installed but the model fails to load (e.g. download
    # failure raising OSError, not RuntimeError): the cloud fallback must
    # kick in instead of start() raising.
    fake = types.ModuleType("faster_whisper")

    class ExplodingWhisperModel:
        def __init__(self, *a, **k):
            raise OSError("model download failed")

    fake.WhisperModel = ExplodingWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    class FakeBackend:
        def transcribe(self, pcm: bytes) -> str:
            return "cloud fallback"

    monkeypatch.setattr(vm.SttStreamer, "_try_cloud",
                        lambda self: FakeBackend())
    s = vm.SttStreamer(prefer_local=True)
    s.start()  # must not raise
    assert s._is_local is False
    s.feed(b"\x00\x00" * 1600)
    assert s.finish() == "cloud fallback"
