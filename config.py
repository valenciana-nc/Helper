import logging
import math
import os
import sys
from logging.handlers import RotatingFileHandler
from collections.abc import Mapping
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlsplit
from model_catalogue import load_provider_catalogue

APP_NAME = "Helper"
ROOT = Path(__file__).parent
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
STARTUP_LOG = LOGS_DIR / "startup.log"
ENV_PATH = ROOT / ".env"
CODEX_MODEL_DEFAULT = "gpt-6-luna"
GROK_MODEL_DEFAULT = "grok-4.7"
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


def _provider_from_values(values: Mapping[str, str] | None = None) -> str:
    value, _source = env_value_with_source("HELPER_PROVIDER", "codex", values)
    return (value or "codex").strip().lower()


def resolve_provider_model(name: str, values: Mapping[str, str] | None = None) -> str:
    provider = _provider_from_values(values)
    defaults = {"anthropic": "claude-sonnet-4-6", "gemini": "gemini-2.5-flash",
                "xai": GROK_MODEL_DEFAULT, "openai_compat": CODEX_MODEL_DEFAULT}
    if provider not in defaults:
        return resolve_codex_model(name, values=values)
    value, _source = env_value_with_source(name, defaults[provider], values)
    return value


def model_compatibility_warnings(values: Mapping[str, str] | None = None) -> list[str]:
    warnings: list[str] = []
    provider = _provider_from_values(values)
    # Gemini values are only invalid for the Codex OAuth path. When the user
    # picked HELPER_PROVIDER=gemini we expect gemini-* model names.
    if provider == "codex":
        for name, fallback, label in (
            ("HELPER_AGENT_MODEL", CODEX_MODEL_DEFAULT, "Codex computer-use"),
            ("HELPER_REASONING_MODEL", CODEX_MODEL_DEFAULT, "Codex chat"),
        ):
            value, source = env_value_with_source(name, fallback, values)
            if source and _is_gemini_model(value):
                warnings.append(
                    f"{source}={value} is not valid for {label}; using {fallback} instead."
                )
    for name, fallback, label in (
        ("HELPER_STT_MODEL", STT_MODEL_DEFAULT, "OpenAI speech-to-text"),
        ("HELPER_TTS_MODEL", TTS_MODEL_DEFAULT, "OpenAI text-to-speech"),
    ):
        value, source = env_value_with_source(name, fallback, values)
        if source and _is_gemini_model(value):
            warnings.append(
                f"{source}={value} is not valid for {label}; using {fallback} instead."
            )
    return warnings


def bool_env(name: str, default: bool = False) -> bool:
    value = env_value(name, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float, *, lo: float, hi: float) -> float:
    """Read a bounded floating-point setting without breaking startup.

    Configuration comes from a user-editable ``.env`` file, so malformed or
    stale values must fall back to a safe default instead of raising during
    module import.  Non-finite values are rejected as well because they can
    poison downstream timing and API parameters.
    """
    raw = env_value(name, str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        logging.getLogger("helper.config").warning(
            "Ignoring invalid numeric setting %s; using the default.", name
        )
        return default
    if not math.isfinite(value):
        logging.getLogger("helper.config").warning(
            "Ignoring non-finite numeric setting %s; using the default.", name
        )
        return default
    return max(lo, min(hi, value))


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    """Read a bounded integer setting without allowing bad ``.env`` values."""
    raw = env_value(name, str(default)).strip()
    try:
        value = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        logging.getLogger("helper.config").warning(
            "Ignoring invalid integer setting %s; using the default.", name
        )
        return default
    if not math.isfinite(float(value)):
        return default
    return max(lo, min(hi, value))


def legacy_env_keys() -> list[str]:
    return sorted(
        key
        for key in os.environ
        if key.startswith("HELPLER_") or key.startswith("HARVIS_")
    )


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

HOTKEY = env_value("HELPER_HOTKEY", "ctrl+shift+space").lower()

AGENT_MODEL = resolve_provider_model("HELPER_AGENT_MODEL")
REASONING_MODEL = resolve_provider_model("HELPER_REASONING_MODEL")
STT_MODEL = resolve_openai_voice_model("HELPER_STT_MODEL", STT_MODEL_DEFAULT)
TTS_MODEL = resolve_openai_voice_model("HELPER_TTS_MODEL", TTS_MODEL_DEFAULT)
TTS_VOICE = env_value("HELPER_TTS_VOICE", "alloy")
SPEAK_TYPED_CHAT = bool_env("HELPER_SPEAK_TYPED_CHAT", False)

AUDIO_SAMPLE_RATE = _int_env("HELPER_AUDIO_SAMPLE_RATE", 16000, lo=8_000, hi=192_000)
AUDIO_CHANNELS = _int_env("HELPER_AUDIO_CHANNELS", 1, lo=1, hi=8)
AUDIO_BLOCKSIZE = _int_env("HELPER_AUDIO_BLOCKSIZE", 0, lo=0, hi=8_192)
AUDIO_SILENCE_THRESHOLD = _int_env("HELPER_AUDIO_SILENCE_THRESHOLD", 700, lo=0, hi=32_767)
AUDIO_MIN_SECONDS = _float_env("HELPER_AUDIO_MIN_SECONDS", 0.25, lo=0.05, hi=60.0)
AUDIO_TRIM_PAD_MS = _int_env("HELPER_AUDIO_TRIM_PAD_MS", 150, lo=0, hi=5_000)

MAX_AGENT_STEPS = _int_env("HELPER_MAX_AGENT_STEPS", 25, lo=1, hi=100)
AGENT_TIMEOUT_SEC = _int_env("HELPER_AGENT_TIMEOUT_SEC", 180, lo=1, hi=3_600)
SCREENSHOT_MAX_EDGE = _int_env("HELPER_SCREENSHOT_MAX_EDGE", 1_280, lo=64, hi=8_192)
HISTORY_MAX_TURNS = _int_env("HELPER_HISTORY_MAX_TURNS", 12, lo=1, hi=1_000)
HISTORY_MAX_TOKENS = _int_env("HELPER_HISTORY_MAX_TOKENS", 12_000, lo=1, hi=200_000)
LOOP_SETTLE_SEC = _float_env("HELPER_LOOP_SETTLE_MS", 350.0, lo=0.0, hi=60_000.0) / 1000.0

API_BASE_URL = env_value("HELPER_API_BASE_URL", "").strip().rstrip("/")
API_KEY = env_value("HELPER_API_KEY", "").strip()
API_MODEL = env_value("HELPER_API_MODEL", "").strip()

PROVIDER = env_value("HELPER_PROVIDER", "codex").strip().lower() or "codex"
ANTHROPIC_API_KEY = env_value("HELPER_ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_AUTH_MODE = env_value("HELPER_ANTHROPIC_AUTH_MODE", "auto").strip().lower()
GEMINI_API_KEY = env_value("HELPER_GEMINI_API_KEY", "").strip()
XAI_API_KEY = env_value("HELPER_XAI_API_KEY", "").strip()
XAI_AUTH_MODE = env_value("HELPER_XAI_AUTH_MODE", "auto").strip().lower()


TEMPERATURE = _float_env("HELPER_TEMPERATURE", 0.7, lo=0.0, hi=2.0)
MAX_TOKENS = _int_env("HELPER_MAX_TOKENS", 4096, lo=1, hi=200_000)

_theme_raw = env_value("HELPER_THEME", "light").strip().lower()
THEME = "dark" if _theme_raw == "dark" else "light"


# Public snapshots provide offline choices; live account refresh can narrow/extend them.
# Listed models do not imply subscription or endpoint access.
PROVIDER_MODELS: dict[str, list[str]] = {
    "codex": [model.id for model in load_provider_catalogue("codex").models],
    "openai_compat": [],
    "xai": [model.id for model in load_provider_catalogue("xai").models],
    "anthropic": [model.id for model in load_provider_catalogue("anthropic").models],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
}


PROVIDER_LABELS: dict[str, str] = {
    "codex": "Codex (ChatGPT sign-in)",
    "openai_compat": "OpenAI-compatible",
    "anthropic": "Anthropic Claude",
    "gemini": "Google Gemini",
    "xai": "Grok (xAI)",
}


def custom_api_enabled() -> bool:
    return bool(API_BASE_URL and API_KEY)


def effective_provider() -> str:
    """Resolve the selected provider, including the legacy custom-endpoint fallback."""
    provider = (PROVIDER or "codex").strip().lower()
    return "openai_compat" if provider == "codex" and custom_api_enabled() else provider


def xai_auth_mode() -> str:
    """Keep existing API-key setups; explicit subscription selection takes precedence."""
    if XAI_AUTH_MODE in {"oauth", "api_key"}:
        return XAI_AUTH_MODE
    return "api_key" if XAI_API_KEY else "oauth"


def anthropic_auth_mode() -> str:
    """Use only the selected Claude connection; preserve existing API-key setups."""
    if ANTHROPIC_AUTH_MODE in {"oauth", "api_key"}:
        return ANTHROPIC_AUTH_MODE
    return "api_key" if ANTHROPIC_API_KEY else "oauth"


def provider_key_configured(provider: str) -> bool:
    """Report locally stored credentials, not verified model access."""
    if provider == "openai_compat":
        return custom_api_enabled()
    if provider == "xai" and xai_auth_mode() == "oauth":
        import oauth_xai
        return oauth_xai.is_signed_in()
    if provider == "anthropic" and anthropic_auth_mode() == "oauth":
        import oauth_anthropic
        return oauth_anthropic.is_signed_in()
    return bool({"anthropic": ANTHROPIC_API_KEY, "gemini": GEMINI_API_KEY, "xai": XAI_API_KEY}.get(provider))


def validate_api_base_url(value: str) -> str | None:
    """Return a user-facing error for an unsafe custom API endpoint.

    Plain HTTP is allowed only for loopback development routers.  Remote
    endpoints must use TLS because the configured API key is sent in a bearer
    header on every request.
    """
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return "API base URL is required"
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "API base URL is malformed"
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password:
        return "API base URL must not include embedded credentials"
    if scheme == "https" and parsed.netloc:
        return None
    if scheme == "http" and host in {"localhost", "127.0.0.1", "::1"} and parsed.netloc:
        return None
    return "API base URL must use HTTPS (or HTTP on localhost only)"

