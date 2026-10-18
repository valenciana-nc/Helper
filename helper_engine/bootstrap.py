"""Composition root: configuration chooses adapters, not dependencies inside the core."""
from __future__ import annotations

from .runtime import Agent, Limits


def _int_env(name: str, default: int | None) -> int | None:
    from config import env_value
    raw = env_value(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}")


def _float_env(name: str, default: float) -> float:
    from config import env_value
    raw = env_value(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}")


def build_agent(*, confirm=None, model_name: str | None = None, backend: str | None = None,
                endpoint: str | None = None, page_index: int | None = None, headless: bool = False):
    from config import AGENT_TIMEOUT_SEC, MAX_AGENT_STEPS, SCREENSHOT_MAX_EDGE, env_value
    from .model import ProviderModel
    from .windows import WindowsDriver
    backend = backend or env_value("HELPER_CONTROL_BACKEND", "desktop")
    endpoint = endpoint if endpoint is not None else env_value("HELPER_BROWSER_CDP_URL", "")
    if page_index is None:
        page_index = _int_env("HELPER_BROWSER_PAGE_INDEX", None)
    if backend not in {"desktop", "browser", "hybrid"}:
        raise ValueError("HELPER_CONTROL_BACKEND must be desktop, browser, or hybrid")
    if backend == "desktop":
        driver = WindowsDriver(max_edge=SCREENSHOT_MAX_EDGE)
    else:
        from .browser import BrowserDriver
        browser = BrowserDriver(endpoint=endpoint, page_index=page_index, headless=headless)
        if backend == "hybrid":
            if not endpoint:
                raise ValueError("Hybrid mode requires an explicit HELPER_BROWSER_CDP_URL")
            from .router import HybridDriver
            driver = HybridDriver(WindowsDriver(max_edge=SCREENSHOT_MAX_EDGE), browser)
        else:
            driver = browser
    limits = Limits(
        max_steps=MAX_AGENT_STEPS,
        max_model_calls=max(3, MAX_AGENT_STEPS + 8),
        max_seconds=_float_env("HELPER_RUN_TIMEOUT_SEC", float(AGENT_TIMEOUT_SEC)),
        help_wait_seconds=_float_env("HELPER_GUIDE_WAIT_SEC", 90.0),
    )
    return Agent(model=ProviderModel(model=model_name), driver=driver, limits=limits, confirm=confirm)
