import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from collections.abc import Mapping
from pathlib import Path
from dotenv import load_dotenv

APP_NAME = "Helper"
ROOT = Path(__file__).parent
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
STARTUP_LOG = LOGS_DIR / "startup.log"
ENV_PATH = ROOT / ".env"
CODEX_MODEL_DEFAULT = "gpt-5.5"
STT_MODEL_DEFAULT = "whisper-1"
TTS_MODEL_DEFAULT = "gpt-4o-mini-tts"

load_dotenv(ENV_PATH)

_LOGGING_CONFIGURED = False


def setup_logging() -> Path:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return STARTUP_LOG
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        STARTUP_LOG, maxBytes=512_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    _LOGGING_CONFIGURED = True
    config_log = logging.getLogger("helper.config")
    config_log.info(
        "Loaded .env from %s (exists=%s)", ENV_PATH, ENV_PATH.exists()
    )
    legacy_keys = legacy_env_keys()
    if legacy_keys:
        config_log.warning(
            "Legacy env keys are present and will be read as fallbacks: %s. "
            "Dashboard saves canonical HELPER_* keys.",
            ", ".join(legacy_keys),
        )
    for warning in model_compatibility_warnings():
        config_log.warning(warning)
    if not OPENAI_API_KEY:
        config_log.info("OPENAI_API_KEY is empty; voice transcription and spoken replies are disabled.")
    return STARTUP_LOG


def _env_candidates(name: str) -> list[str]:
    candidates = [name]
    if name.startswith("HELPER_"):
        suffix = name.removeprefix("HELPER_")
        candidates.extend([f"HELPLER_{suffix}", f"HARVIS_{suffix}"])
    return candidates


def env_value_with_source(
    name: str,
    default: str = "",
    values: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    """Read canonical HELPER_* settings with legacy HELPLER/HARVIS fallbacks."""
    source = values if values is not None else os.environ
    for candidate in _env_candidates(name):
        value = source.get(candidate)
        if value not in (None, ""):
            return value, candidate
    return default, None


def env_value(name: str, default: str = "") -> str:
    value, _source = env_value_with_source(name, default)
    return value


def _is_gemini_model(model: str) -> bool:
    value = (model or "").strip().lower()
    return value.startswith("gemini-") or "gemini" in value


def _is_safe_openai_model(model: str) -> bool:
    return bool((model or "").strip()) and not _is_gemini_model(model)


def resolve_codex_model(
    name: str,
    default: str = CODEX_MODEL_DEFAULT,
    values: Mapping[str, str] | None = None,
) -> str:
    value, _source = env_value_with_source(name, default, values)
    return codex_request_model(value, default)


def codex_request_model(model: str, default: str = CODEX_MODEL_DEFAULT) -> str:
    if not _is_safe_openai_model(model):
        return default
    return model


def resolve_openai_voice_model(
    name: str,
    default: str,
    values: Mapping[str, str] | None = None,
) -> str:
    value, _source = env_value_with_source(name, default, values)
    if not _is_safe_openai_model(value):
        return default
    return value


def model_compatibility_warnings(values: Mapping[str, str] | None = None) -> list[str]:
    warnings: list[str] = []
    checks = (
        ("HELPER_AGENT_MODEL", CODEX_MODEL_DEFAULT, "Codex computer-use"),
        ("HELPER_REASONING_MODEL", CODEX_MODEL_DEFAULT, "Codex chat"),
        ("HELPER_STT_MODEL", STT_MODEL_DEFAULT, "OpenAI speech-to-text"),
        ("HELPER_TTS_MODEL", TTS_MODEL_DEFAULT, "OpenAI text-to-speech"),
    )
    for name, fallback, label in checks:
        value, source = env_value_with_source(name, fallback, values)
        if source and _is_gemini_model(value):
            warnings.append(
                f"{source}={value} is not valid for {label}; using {fallback} instead."
            )
    return warnings


def bool_env(name: str, default: bool = False) -> bool:
    value = env_value(name, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def legacy_env_keys() -> list[str]:
    return sorted(
        key
        for key in os.environ
        if key.startswith("HELPLER_") or key.startswith("HARVIS_")
    )


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

HOTKEY = env_value("HELPER_HOTKEY", "ctrl+shift+space").lower()
DEFAULT_MODE = env_value("HELPER_DEFAULT_MODE", "help").lower()

AGENT_MODEL = resolve_codex_model("HELPER_AGENT_MODEL", CODEX_MODEL_DEFAULT)
REASONING_MODEL = resolve_codex_model("HELPER_REASONING_MODEL", CODEX_MODEL_DEFAULT)
STT_MODEL = resolve_openai_voice_model("HELPER_STT_MODEL", STT_MODEL_DEFAULT)
TTS_MODEL = resolve_openai_voice_model("HELPER_TTS_MODEL", TTS_MODEL_DEFAULT)
TTS_VOICE = env_value("HELPER_TTS_VOICE", "alloy")
SPEAK_TYPED_CHAT = bool_env("HELPER_SPEAK_TYPED_CHAT", False)

AUDIO_SAMPLE_RATE = int(env_value("HELPER_AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHANNELS = int(env_value("HELPER_AUDIO_CHANNELS", "1"))
AUDIO_BLOCKSIZE = int(env_value("HELPER_AUDIO_BLOCKSIZE", "0"))
AUDIO_SILENCE_THRESHOLD = int(env_value("HELPER_AUDIO_SILENCE_THRESHOLD", "700"))
AUDIO_MIN_SECONDS = float(env_value("HELPER_AUDIO_MIN_SECONDS", "0.25"))
AUDIO_TRIM_PAD_MS = int(env_value("HELPER_AUDIO_TRIM_PAD_MS", "150"))

MAX_AGENT_STEPS = int(env_value("HELPER_MAX_AGENT_STEPS", "25"))
AGENT_TIMEOUT_SEC = int(env_value("HELPER_AGENT_TIMEOUT_SEC", "180"))
SCREENSHOT_MAX_EDGE = int(env_value("HELPER_SCREENSHOT_MAX_EDGE", "1280"))
HISTORY_MAX_TURNS = int(env_value("HELPER_HISTORY_MAX_TURNS", "12"))
HISTORY_MAX_TOKENS = int(env_value("HELPER_HISTORY_MAX_TOKENS", "12000"))
LOOP_SETTLE_SEC = max(0.0, float(env_value("HELPER_LOOP_SETTLE_MS", "350")) / 1000.0)
USE_ROUTE_CLASSIFIER = bool_env("HELPER_ROUTE_CLASSIFIER", True)

API_BASE_URL = env_value("HELPER_API_BASE_URL", "").strip().rstrip("/")
API_KEY = env_value("HELPER_API_KEY", "").strip()
API_MODEL = env_value("HELPER_API_MODEL", "").strip()


def custom_api_enabled() -> bool:
    return bool(API_BASE_URL and API_KEY)


DESTRUCTIVE_KEYWORDS = ("delete", "remove", "rm ", "send", "buy", "purchase", "format", "shutdown")


def assert_auth() -> None:
    import token_store

    if token_store.load() is None:
        raise RuntimeError(
            "Not signed in. Open the dashboard and click 'Sign in with ChatGPT' to continue."
        )
