import io
import threading
import wave
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from config import (
    AUDIO_BLOCKSIZE,
    AUDIO_CHANNELS,
    AUDIO_MIN_SECONDS,
    AUDIO_SAMPLE_RATE,
    AUDIO_SILENCE_THRESHOLD,
    AUDIO_TRIM_PAD_MS,
    OPENAI_API_KEY,
    STT_MODEL,
    TTS_MODEL,
    TTS_VOICE,
)
from openai_client import AudioUnavailable, OpenAIClient


@dataclass(frozen=True)
class RecordedAudio:
    wav_bytes: bytes
    sample_rate: int
    channels: int
    duration_sec: float


@dataclass(frozen=True)
class SynthesizedSpeech:
    audio_bytes: bytes
    mime_type: str
    text: str


class AudioHandler:
    def __init__(
        self,
        *,
        api_key: str = OPENAI_API_KEY,
        stt_model: str = STT_MODEL,
        tts_model: str = TTS_MODEL,
        tts_voice: str = TTS_VOICE,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        channels: int = AUDIO_CHANNELS,
        blocksize: int = AUDIO_BLOCKSIZE,
        silence_threshold: int = AUDIO_SILENCE_THRESHOLD,
        min_seconds: float = AUDIO_MIN_SECONDS,
        trim_pad_ms: int = AUDIO_TRIM_PAD_MS,
    ) -> None:
        self._client = OpenAIClient(optional_api_key=api_key or None)
        self._available = bool(api_key)
        self._stt_model = stt_model
        self._tts_model = tts_model
        self._tts_voice = tts_voice
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._silence_threshold = silence_threshold
        self._min_seconds = min_seconds
        self._trim_pad_ms = trim_pad_ms
        self._lock = threading.Lock()
        self._playback_lock = threading.Lock()
        self._playback_stop = threading.Event()
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._speaking = False

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._speaking

    def start_recording(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._chunks = []
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                blocksize=self._blocksize,
                callback=self._on_audio,
            )
            self._stream.start()
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

        recording = self._recorded_audio(audio)
        if recording is None:
            raise RuntimeError("Recording was too short after trimming silence.")
        return recording

    def snapshot_recording(self) -> RecordedAudio | None:
        with self._lock:
            if not self._recording or not self._chunks:
                return None
            audio = np.concatenate(self._chunks, axis=0)

        return self._recorded_audio(audio)

    def _recorded_audio(self, audio: np.ndarray) -> RecordedAudio | None:
        trimmed = self._trim_silence(audio)
        duration_sec = len(trimmed) / self._sample_rate
        if duration_sec < self._min_seconds:
            return None

        wav_bytes = self._to_wav_bytes(trimmed)
        return RecordedAudio(
            wav_bytes=wav_bytes,
            sample_rate=self._sample_rate,
            channels=self._channels,
            duration_sec=duration_sec,
        )

    def transcribe(self, recording: RecordedAudio) -> str:
        try:
            return self._client.transcribe(recording.wav_bytes, model=self._stt_model)
        except AudioUnavailable as exc:
            raise RuntimeError(str(exc)) from exc

    def synthesize(self, text: str) -> SynthesizedSpeech:
        text = text.strip()
        if not text:
            raise ValueError("Expected non-empty text for speech synthesis.")
        try:
            audio_bytes, mime_type = self._client.synthesize(text, model=self._tts_model, voice=self._tts_voice)
        except AudioUnavailable as exc:
            raise RuntimeError(str(exc)) from exc
        if not audio_bytes:
            raise RuntimeError("TTS returned an empty audio payload.")
        return SynthesizedSpeech(audio_bytes=audio_bytes, mime_type=mime_type, text=text)

    def speak_text(self, text: str) -> SynthesizedSpeech:
        speech = self.synthesize(text)
        self.play_speech(speech)
        return speech

    def play_speech(self, speech: SynthesizedSpeech) -> None:
        samples, sample_rate = self._decode_audio_blob(speech.audio_bytes, speech.mime_type)
        self.stop_playback()
        with self._playback_lock:
            self._playback_stop.clear()
            with self._lock:
                self._speaking = True
            try:
                sd.play(samples, samplerate=sample_rate, blocking=False)
                while True:
                    if self._playback_stop.wait(0.05):
                        sd.stop()
                        return
                    stream = sd.get_stream()
                    if stream is None or not stream.active:
                        return
            finally:
                sd.stop()
                with self._lock:
                    self._speaking = False

    def stop_playback(self) -> None:
        self._playback_stop.set()
        sd.stop()

    def _finalize_stream(self) -> None:
        stream: sd.InputStream | None
        with self._lock:
            stream = self._stream
            self._stream = None
            self._recording = False
        if stream is None:
            return
        stream.stop()
        stream.close()

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        del frames, time_info, status
        with self._lock:
            if not self._recording:
                return
            self._chunks.append(indata.copy())

    def _trim_silence(self, audio: np.ndarray) -> np.ndarray:
        mono = np.abs(audio.astype(np.int32))
        if mono.ndim > 1:
            mono = mono.max(axis=1)
        above_threshold = np.flatnonzero(mono >= self._silence_threshold)
        if above_threshold.size == 0:
            return audio

        pad_samples = int(self._sample_rate * self._trim_pad_ms / 1000)
        start = max(int(above_threshold[0]) - pad_samples, 0)
        end = min(int(above_threshold[-1]) + pad_samples + 1, len(audio))
        return audio[start:end]

    def _to_wav_bytes(self, audio: np.ndarray) -> bytes:
        pcm = np.ascontiguousarray(audio.astype(np.int16))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(self._channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return buf.getvalue()

    def _decode_audio_blob(self, audio_bytes: bytes, mime_type: str) -> tuple[np.ndarray, int]:
        normalized = (mime_type or "").lower()
        if normalized in {"audio/wav", "audio/x-wav", "audio/wave"}:
            return self._decode_wav(audio_bytes)
        raise RuntimeError(f"Unsupported TTS audio format: {mime_type or 'unknown'}")

    @staticmethod
    def _decode_wav(audio_bytes: bytes) -> tuple[np.ndarray, int]:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            frames = wav_file.readframes(frame_count)

        dtype, scale = AudioHandler._pcm_format(sample_width)
        raw = np.frombuffer(frames, dtype=dtype)
        if sample_width == 1:
            audio = (raw.astype(np.float32) - scale) / scale
        else:
            audio = raw.astype(np.float32) / scale
        if channels > 1:
            audio = audio.reshape(-1, channels)
        return audio, sample_rate

    @staticmethod
    def _pcm_format(sample_width: int) -> tuple[np.dtype, float]:
        if sample_width == 1:
            return np.uint8, 128.0
        if sample_width == 2:
            return np.int16, 32768.0
        if sample_width == 4:
            return np.int32, 2147483648.0
        raise RuntimeError(f"Unsupported WAV sample width: {sample_width}")
