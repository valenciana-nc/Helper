"""v2 actuator: UIA element patterns first, synthetic input as fallback.

Element patterns (Invoke/SetValue) move no cursor and are DPI-proof.
Synthetic pyautogui input covers canvas/custom controls. All imports lazy
so `import v2.actuator` is safe anywhere.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("helper.v2.actuator")


class V2Actuator:
    """Closed-loop actuator implementing the v2 Actuator protocol."""

    def __init__(self) -> None:
        self._use_uia = sys.platform == "win32"
        if self._use_uia:
            try:
                import comtypes.client  # noqa: F401
            except Exception:
                self._use_uia = False

    @property
    def uia_available(self) -> bool:
        return self._use_uia

    # -- element patterns (no cursor movement, DPI-proof) --

    def click_element(self, el) -> bool:
        if self._use_uia:
            try:
                return self._uia_activate(el, "invoke")
            except Exception as exc:
                log.debug("UIA invoke failed: %r", exc)
        return False

    def set_value(self, el, text: str) -> bool:
        if self._use_uia:
            try:
                return self._uia_activate(el, "set_value", text)
            except Exception as exc:
                log.debug("UIA set_value failed: %r", exc)
        return False

    # -- synthetic fallback --

    def click_at(self, x: int, y: int) -> None:
        import pyautogui

        pyautogui.click(int(x), int(y))

    def type_text(self, text: str) -> None:
        import pyautogui

        # Chunk long text; pyautogui.write handles unicode via clipboard
        # fallback internally on some platforms, but keep it simple.
        pyautogui.write(text)

    def press_key(self, key: str) -> None:
        import pyautogui

        pyautogui.press(key)

    # -- UIA internals (Windows only) --

    def _find_descendant(self, el):
        """Locate the pywinauto wrapper matching el.id == '{pid}:{runtime_id}'."""
        from pywinauto import Desktop

        pid, runtime = el.id.split(":", 1)
        for win in Desktop(backend="uia").windows():
            try:
                if str(win.process_id()) != pid:
                    continue
                for desc in win.descendants():
                    try:
                        rid = desc.element_info.runtime_id
                        rid_str = ".".join(str(v) for v in rid) \
                            if isinstance(rid, (list, tuple)) else str(rid)
                        if rid_str == runtime:
                            return desc
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def _uia_activate(self, el, action: str, text: str = "") -> bool:
        desc = self._find_descendant(el)
        if desc is None:
            return False
        if action == "invoke":
            try:
                desc.invoke()
                return True
            except Exception:
                # Fallback: legacy click pattern via rect center is handled
                # by the caller (synthetic path).
                return False
        if action == "set_value":
            try:
                desc.set_edit_text(text)
                return True
            except Exception:
                try:
                    desc.set_focus()
                    return False  # caller falls back to click-then-type
                except Exception:
                    return False
        return False
