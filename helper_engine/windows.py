"""Foreground-scoped Windows UIA and guarded visual input; no imports touch the desktop."""
from __future__ import annotations

import ctypes
import io
import os
import sys
import uuid
from ctypes import wintypes
from urllib.parse import urlsplit

from .perception import image_difference, target_rect
from .protocol import ProtocolError
from .types import Effect, Element, Execution, Observation, Rect, StaleObservation
from .uia_worker import UiaClient


class WindowsDriver:
    def __init__(self, *, uia=None, max_edge: int = 1600):
        if max_edge < 64:
            raise ValueError("max_edge must be at least 64 pixels")
        self.uia = uia or UiaClient()
        self.max_edge = max_edge
        self._last_hwnd = 0
        self._current_id = ""
        self._hwnd = 0
        if sys.platform == "win32":
            try:
                # The CLI does not import main.py's DPI setup.
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

    @staticmethod
    def _api():
        if sys.platform != "win32":
            raise RuntimeError("Desktop control currently requires Windows; use the browser adapter on other OSes.")
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindow.restype = wintypes.HWND
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        return user32

    @staticmethod
    def _pid(api, hwnd) -> int:
        pid = wintypes.DWORD()
        api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _scope(self):
        api = self._api()
        hwnd = api.GetForegroundWindow()
        if hwnd and self._pid(api, hwnd) != os.getpid():
            self._last_hwnd = hwnd
            return hwnd
        if self._last_hwnd and api.IsWindowVisible(self._last_hwnd) \
                and self._pid(api, self._last_hwnd) != os.getpid():
            return self._last_hwnd
        # Helper's own chat/orb must not become an action target. (The PID
        # check above also guards against HWND reuse: a recycled handle for
        # our own window must not be returned here.)
        cursor = hwnd
        for _ in range(80):
            cursor = api.GetWindow(cursor, 2)  # GW_HWNDNEXT; z order
            if not cursor:
                break
            if api.IsWindowVisible(cursor) and self._pid(api, cursor) != os.getpid():
                rectangle = wintypes.RECT()
                if api.GetWindowRect(cursor, ctypes.byref(rectangle)) and rectangle.right - rectangle.left > 100:
                    self._last_hwnd = cursor
                    return cursor
        raise RuntimeError("No external visible window is available.")

    def _capture(self, hwnd):
        import mss
        from PIL import Image
        api = self._api()
        rectangle = wintypes.RECT()
        api.GetWindowRect(hwnd, ctypes.byref(rectangle))
        cx, cy = (rectangle.left + rectangle.right) // 2, (rectangle.top + rectangle.bottom) // 2
        with mss.mss() as capture:
            monitor = next(
                (m for m in capture.monitors[1:] if m["left"] <= cx < m["left"] + m["width"]
                 and m["top"] <= cy < m["top"] + m["height"]), capture.monitors[1])
            raw = capture.grab(monitor)
            image = Image.frombytes("RGB", raw.size, raw.rgb)
        scale = min(1.0, self.max_edge / max(image.size))
        if scale < 1:
            image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="PNG", compress_level=2)
        return out.getvalue(), image.width, image.height, monitor["left"], monitor["top"], scale

    def observe(self) -> Observation:
        """Capture the foreground window: screenshot plus UIA control tree."""
        hwnd = self._scope()
        api = self._api()
        token = uuid.uuid4().hex
        warnings = []
        try:
            snapshot = self.uia.request("snapshot", hwnd=int(hwnd), token=token)
            if snapshot.get("status") == "unavailable":
                warnings.append("Accessibility unavailable; use current screenshot targets.")
            if snapshot.get("truncated"):
                warnings.append("Accessibility snapshot is bounded; unlisted controls may still exist.")
        except Exception:
            snapshot = {}
            warnings.append("Accessibility timed out; use current screenshot targets.")
        png, width, height, left, top, scale = self._capture(hwnd)
        title = ctypes.create_unicode_buffer(1024)
        api.GetWindowTextW(hwnd, title, len(title))
        elements = tuple(
            Element(**{**item, "rect": Rect(*item["rect"]), "capabilities": tuple(item["capabilities"])})
            for item in snapshot.get("elements", [])
            if (item["rect"][0] < left + width / scale and item["rect"][0] + item["rect"][2] > left
                and item["rect"][1] < top + height / scale and item["rect"][1] + item["rect"][3] > top)
        )
        self._hwnd, self._current_id = hwnd, token
        return Observation(token, "desktop", str(int(hwnd)), title.value, png, width, height, elements,
                           left, top, scale, warnings=tuple(warnings))

    def validate(self, action, observation):
        """Reject the action if the scene changed since the decision's observation."""
        if observation.id != self._current_id or str(int(self._scope())) != observation.scope:
            raise StaleObservation("The foreground window or observation changed")
        try:
            rect = target_rect(action, observation)
        except ProtocolError as exc:
            # Target vanished/disabled since the decision: stale scene, not an
            # ambiguous side effect. Must not become Effect.UNKNOWN.
            raise StaleObservation(f"Target no longer valid: {exc}") from exc
        if action.target and action.target.element_id:
            try:
                result = self.uia.request("validate", token=observation.id, element_id=action.target.element_id)
            except Exception as exc:
                # Validation never dispatches input; a dead UIA lease requires a new snapshot, not an unknown effect.
                raise StaleObservation("Accessibility validation was unavailable; observe again") from exc
            if result.get("status") != "valid":
                raise StaleObservation("The accessibility target changed")
        # A fresh image catches canvas changes, scrolls, occlusion and stale keyboard focus.
        png, width, height, left, top, scale = self._capture(self._hwnd)
        if (width, height, left, top, scale) != (
                observation.width, observation.height, observation.origin_x, observation.origin_y, observation.scale):
            raise StaleObservation("Display geometry changed")
        crop = observation.image_box(rect) if rect else None
        if image_difference(observation.png, png, box=crop) > 0.07:
            raise StaleObservation("The visible action region changed")

    def _focus(self):
        api = self._api()
        if api.GetForegroundWindow() != self._hwnd:
            api.SetForegroundWindow(self._hwnd)
        if api.GetForegroundWindow() != self._hwnd:
            raise StaleObservation("Windows did not grant foreground input to the target app")

    def execute(self, action, observation):
        """Dispatch one typed action via UIA patterns or synthetic input."""
        target = action.target
        if target and target.element_id and action.kind in {"click", "fill"}:
            result = self.uia.request("act", token=observation.id, element_id=target.element_id,
                                      kind=action.kind, text=action.text)
            status = result.get("status")
            if status == "stale":
                raise StaleObservation("The native target expired")
            if status in {"applied", "unknown"}:
                return Execution(Effect(status), "uia", result.get("detail", ""))
            if status != "unsupported":
                return Execution(Effect.REJECTED, "uia", "Native target unavailable; observe again.")
            # Only an explicit pre-dispatch unsupported response permits a fallback.
        if action.kind == "open":
            from launch_safety import HELP_LAUNCH_COMMANDS, launch_target
            value = action.text.strip()
            parsed = urlsplit(value)
            if value.casefold() not in HELP_LAUNCH_COMMANDS and parsed.scheme.casefold() not in {
                    "http", "https", "ms-settings"}:
                return Execution(Effect.REJECTED, "launch", "Choose an approved app name or http(s)/Settings URL.")
            launch_target(value, allowed_commands=HELP_LAUNCH_COMMANDS)
            return Execution(Effect.APPLIED, "launch")
        self._focus()
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0
        try:
            rect = target_rect(action, observation)
        except ProtocolError as exc:
            raise StaleObservation(f"Target no longer valid: {exc}") from exc
        if action.kind in {"click", "double_click", "right_click"}:
            x, y = rect.center
            pyautogui.click(x, y, clicks=2 if action.kind == "double_click" else 1,
                           interval=0.08, button="right" if action.kind == "right_click" else "left")
        elif action.kind == "fill":
            pyautogui.click(*rect.center)
            pyautogui.hotkey("ctrl", "a")
            self._type_unicode(action.text)
        elif action.kind == "press":
            aliases = {"control": "ctrl", "escape": "esc", "return": "enter", "meta": "win", "delete": "delete"}
            keys = [aliases.get(key.casefold(), key.casefold()) for key in action.keys]
            if any(key not in pyautogui.KEYBOARD_KEYS for key in keys):
                return Execution(Effect.REJECTED, "input", "Unsupported key name")
            pyautogui.hotkey(*keys)
        elif action.kind == "scroll":
            if rect:
                pyautogui.moveTo(*rect.center)
            # Protocol uses pixels; Windows wheel units are approximately 120 px.
            pyautogui.scroll(-max(1, round(abs(action.amount) / 120)) * (1 if action.amount > 0 else -1))
        elif action.kind == "drag":
            pyautogui.moveTo(*rect.center)
            destination = observation.screen_box(action.destination)
            pyautogui.dragTo(*destination.center, duration=0.25)
        else:
            return Execution(Effect.REJECTED, "input", "Unsupported operation")
        return Execution(Effect.APPLIED, "input")

    @staticmethod
    def _type_unicode(text: str):
        import time
        import pyautogui
        import pyperclip
        previous = pyperclip.paste()
        try:
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.06)
        finally:
            if pyperclip.paste() == text:
                pyperclip.copy(previous)

    def close(self):
        self.uia.close()
        self._current_id = ""
