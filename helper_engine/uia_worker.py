"""A disposable UIA process owns COM and cached controls.

UIA providers can hang inside a native call. A thread timeout cannot cancel that
call. The parent bounds each request and replaces the worker after a timeout;
it never retries an input request whose outcome may be unknown.
"""
from __future__ import annotations

import multiprocessing
import time
from collections import deque


class UiaTimeout(TimeoutError):
    pass


class UiaClient:
    def __init__(self, timeout: float = 2.5):
        self.timeout = timeout
        self._process = None
        self._pipe = None

    def request(self, operation: str, **payload) -> dict:
        if self._process is None or not self._process.is_alive():
            self.close()
            ctx = multiprocessing.get_context("spawn")
            parent, child = ctx.Pipe()
            self._process = ctx.Process(target=_worker, args=(child,), daemon=True, name="helper-uia")
            self._process.start()
            child.close()
            self._pipe = parent
        try:
            self._pipe.send({"operation": operation, **payload})
            if not self._pipe.poll(self.timeout):
                raise UiaTimeout(f"UI Automation {operation} exceeded {self.timeout:g}s")
            return self._pipe.recv()
        except (EOFError, BrokenPipeError, OSError, UiaTimeout):
            self.close()
            raise

    def close(self):
        process, self._process = self._process, None
        pipe, self._pipe = self._pipe, None
        try:
            if pipe is not None:
                pipe.close()
        finally:
            # Process cleanup must not be skipped if pipe.close() raises;
            # otherwise the worker leaks as an orphaned helper-uia process.
            if process is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.5)


def _property(wrapper, prop: int, default=None):
    try:
        return wrapper.element_info.element.GetCurrentPropertyValue(prop)
    except Exception:
        return default


def _record(wrapper, mark: str) -> dict | None:
    info = wrapper.element_info
    rectangle = wrapper.rectangle()
    width, height = rectangle.width(), rectangle.height()
    visible, enabled = wrapper.is_visible(), wrapper.is_enabled()
    if width <= 1 or height <= 1 or not visible:
        return None
    password = bool(_property(wrapper, 30019, False))
    value = None if password else _property(wrapper, 30045)
    capabilities = []
    for name, attr in (("click", "iface_invoke"), ("select", "iface_selection_item"),
                       ("toggle", "iface_toggle"), ("fill", "iface_value")):
        try:
            if getattr(wrapper, attr) is not None:
                capabilities.append(name)
        except Exception:
            pass
    checked = None
    try:
        checked = bool(wrapper.iface_toggle.CurrentToggleState)
    except Exception:
        pass
    return {"id": mark, "name": str(info.name or "")[:500], "role": str(info.control_type or ""),
            "rect": [rectangle.left, rectangle.top, width, height],
            "value": str(value)[:4000] if value is not None else None,
            "enabled": bool(enabled), "visible": bool(visible), "password": password,
            "focused": bool(_property(wrapper, 30008, False)), "checked": checked,
            "capabilities": capabilities}


def _worker(pipe):
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="uia")
        cache = {}
        identities = {}
        records = {}
        token = ""
        while True:
            request = pipe.recv()
            operation = request["operation"]
            if operation == "snapshot":
                cache, identities, records = {}, {}, {}
                token = request["token"]
                root = desktop.window(handle=request["hwnd"]).wrapper_object()
                pending = deque([(root, 0)])
                until = time.monotonic() + 0.8
                while pending and len(cache) < 160 and time.monotonic() < until:
                    wrapper, depth = pending.popleft()
                    try:
                        mark = f"u{len(cache) + 1}"
                        item = _record(wrapper, mark)
                        if item:
                            cache[mark] = wrapper
                            identities[mark] = tuple(wrapper.element_info.element.GetRuntimeId())
                            records[mark] = item
                        if depth < 8:
                            pending.extend((child, depth + 1) for child in wrapper.children()[:160])
                    except Exception:
                        continue
                pipe.send({"elements": list(records.values()), "truncated": bool(pending)})
                continue
            if request.get("token") != token or request.get("element_id") not in cache:
                pipe.send({"status": "stale", "detail": "UIA target lease expired"})
                continue
            mark = request["element_id"]
            wrapper = cache[mark]
            try:
                live = _record(wrapper, mark)
                if (not live or not live["enabled"] or live["name"] != records[mark]["name"]
                        or live["role"] != records[mark]["role"]
                        or live["rect"] != records[mark]["rect"]
                        or tuple(wrapper.element_info.element.GetRuntimeId()) != identities[mark]):
                    pipe.send({"status": "stale", "detail": "UIA target changed"})
                    continue
            except Exception:
                pipe.send({"status": "stale", "detail": "UIA target no longer exists"})
                continue
            if operation == "validate":
                pipe.send({"status": "valid"})
                continue
            kind = request["kind"]
            # Choose a supported pattern BEFORE dispatch; an exception after dispatch is ambiguous.
            native = None
            if kind == "click":
                for capability, method in (("click", "invoke"), ("select", "select"), ("toggle", "toggle")):
                    if capability in live["capabilities"]:
                        native = getattr(wrapper, method, None)
                        if native is not None:
                            break
            elif kind == "fill" and "fill" in live["capabilities"]:
                def native():
                    wrapper.iface_value.SetValue(request["text"])
            if native is None:
                pipe.send({"status": "unsupported"})
                continue
            try:
                native()
            except Exception as exc:
                pipe.send({"status": "unknown", "detail": type(exc).__name__})
            else:
                pipe.send({"status": "applied"})
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            pipe.send({"status": "unavailable", "detail": type(exc).__name__})
        except Exception:
            pass
    finally:
        pipe.close()
