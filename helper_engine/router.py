"""Optional DOM enrichment when an explicitly selected browser tab matches the active app."""
from __future__ import annotations

from .types import StaleObservation

# Window title markers for common browsers. The browser adapter only enriches
# when it can match an explicit tab — never guessing an authenticated session.
BROWSER_TITLE_MARKERS = (
    "Google Chrome", "Microsoft Edge", "Mozilla Firefox", "Brave", "Opera",
    "Vivaldi", "Arc",
)


class HybridDriver:
    def __init__(self, desktop, browser):
        self.desktop, self.browser = desktop, browser
        self._selected = None

    def observe(self):
        observation = self.desktop.observe()
        self._selected = self.desktop
        # Never guess an authenticated tab. The browser adapter selects one explicit page.
        if any(marker in observation.title for marker in BROWSER_TITLE_MARKERS):
            try:
                if self.browser.matches_window_title(observation.title):
                    enriched = self.browser.observe()
                    self._selected = self.browser
                    return enriched
            except Exception:
                # This is an observation fallback before any action, not an action retry.
                pass
        return observation

    def validate(self, action, observation):
        if self._selected is None:
            raise StaleObservation("No observed surface")
        self._selected.validate(action, observation)

    def execute(self, action, observation):
        if self._selected is None:
            raise StaleObservation("No observed surface")
        return self._selected.execute(action, observation)

    def close(self):
        try:
            self.desktop.close()
        finally:
            self.browser.close()
            self._selected = None
