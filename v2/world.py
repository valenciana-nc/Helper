"""Helper-E v2 — persistent world model.

Keeps a live model of the desktop: a screenshot plus the UI-element tree,
re-observed on a background thread. New :class:`ScreenFrame`s are published
only when the element tree actually changes, so consumers (planner,
executor, verifier) never re-derive the world from scratch between steps.

Platform behavior:
- Windows: element tree comes from UIA (pywinauto/comtypes); refresh is
  driven by UIA structure-changed events when subscription succeeds,
  falling back to polling.
- Other platforms: polling only; the tree is empty (vision-fallback path),
  but screenshots and change signaling still work.

No Qt, no network, no LLM in this module. Importing it never touches the
display, the network, or platform APIs.
"""

from __future__ import annotations

import io
import logging
import sys
import threading
import time
from dataclasses import dataclass, field

from PIL import Image

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Element:  # one UI element, stable identity
    id: str  # stable within a session: f"{pid}:{runtime_id}"
    control_type: str
    name: str
    rect: tuple[int, int, int, int]  # screen px: x, y, w, h
    enabled: bool
    visible: bool
    value: str | None  # text/value pattern if available


@dataclass
class ScreenFrame:  # one observed moment
    seq: int
    png: bytes  # full-frame PNG (encoded ONCE per frame)
    width: int
    height: int
    elements: dict[str, Element]  # id -> Element, from tree snapshot
    changed_ids: set[str] = field(default_factory=set)  # ids added/removed/changed since last frame


def normalized_control_type(control_type: str) -> str:
    """Canonical control-type key: lowercase, alphanumeric-only.

    UIA reports types like "Button"/"Edit"/"SplitButton"; every consumer
    (annotation in observe.py, actuation in ground.py) must compare the
    same way, so this lives next to Element instead of being copied
    per-module.
    """
    return "".join(ch for ch in control_type.lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# Capture + tree snapshot (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------


def _placeholder_png() -> bytes:
    img = Image.new("RGB", (1, 1), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _capture_png() -> tuple[bytes, int, int]:
    """Capture the virtual desktop. Returns (png_bytes, width, height).

    Raises on failure; callers substitute a placeholder so the refresh loop
    keeps running (e.g. on headless machines). The import is lazy so that
    importing this module never touches v1 or the display.
    """
    from screen import capture_virtual_desktop  # noqa: PLC0415  (v1 reuse)

    cap = capture_virtual_desktop()
    return cap.png_bytes, cap.width, cap.height


def _try_uia_value(wrapper) -> str | None:
    """Best-effort read of the UIA Value pattern (Windows only)."""
    try:
        element = wrapper.element_info.element
        # UIA_ValueValuePropertyId
        val = element.GetCurrentPropertyValue(30045)
    except Exception:
        return None
    if val is None:
        return None
    try:
        text = str(val).strip()
    except Exception:
        return None
    return text or None


def _snapshot_tree_windows(
    *, deadline_s: float = 0.45, max_elements: int = 400
) -> dict[str, Element]:
    """Walk the UIA tree once (Windows only). Never raises."""
    try:
        from pywinauto import Desktop  # noqa: PLC0415
    except Exception:
        log.debug("pywinauto unavailable; empty tree", exc_info=True)
        return {}
    out: dict[str, Element] = {}
    stop_at = time.monotonic() + deadline_s
    try:
        descendants = Desktop(backend="uia").descendants()
    except Exception:
        log.debug("UIA desktop walk failed", exc_info=True)
        return {}
    for wrapper in descendants:
        if len(out) >= max_elements or time.monotonic() > stop_at:
            break
        try:
            info = wrapper.element_info
            pid = int(info.process_id or 0)
            runtime_id = ".".join(str(int(x)) for x in info.element.GetRuntimeId())
            eid = f"{pid}:{runtime_id}"
            rect = wrapper.rectangle()
            out[eid] = Element(
                id=eid,
                control_type=str(info.control_type),
                name=str(info.name or ""),
                rect=(
                    int(rect.left),
                    int(rect.top),
                    int(rect.right - rect.left),
                    int(rect.bottom - rect.top),
                ),
                enabled=bool(info.enabled),
                visible=bool(info.visible),
                value=_try_uia_value(wrapper),
            )
        except Exception:
            continue
    return out


def _snapshot_tree() -> dict[str, Element]:
    """Current UI-element tree. Empty dict when UIA is unavailable."""
    if sys.platform != "win32":
        return {}
    try:
        return _snapshot_tree_windows()
    except Exception:
        log.debug("UIA snapshot failed", exc_info=True)
        return {}


def _diff_elements(
    old: dict[str, Element], new: dict[str, Element]
) -> set[str]:
    """Ids added, removed, or changed (rect/value/name/type/flags)."""
    changed: set[str] = set()
    for eid, element in new.items():
        previous = old.get(eid)
        if previous is None or previous != element:
            changed.add(eid)
    for eid in old:
        if eid not in new:
            changed.add(eid)
    return changed


# ---------------------------------------------------------------------------
# UIA structure-changed event source (Windows, best-effort)
# ---------------------------------------------------------------------------


class _UiaEventSource:
    """Best-effort UIA structure-changed subscription (Windows only).

    When subscription succeeds, :meth:`wait` wakes promptly on tree changes;
    the refresh loop still uses it with a poll-interval timeout, so event
    failure or absence degrades cleanly to pure polling.
    """

    def __init__(self) -> None:
        self._fired = threading.Event()
        self._uia = None
        self._handler = None
        self._root = None
        self.available = False
        if sys.platform != "win32":
            return
        try:
            self._subscribe()
        except Exception:
            log.debug(
                "UIA event subscription unavailable; using polling", exc_info=True
            )
            self._teardown()
        else:
            self.available = True

    def _subscribe(self) -> None:
        import comtypes  # noqa: PLC0415
        import comtypes.client  # noqa: PLC0415

        try:
            from comtypes.gen import UIAutomationClient as UIA  # noqa: PLC0415
        except ImportError:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA  # noqa: PLC0415

        uia = comtypes.client.CreateObject(
            UIA.CUIAutomation, interface=UIA.IUIAutomation
        )
        fired = self._fired

        class _Handler:
            _com_interfaces_ = [UIA.IUIAutomationStructureChangedEventHandler]

            def HandleStructureChangedEvent(self, sender, change_type, runtime_id):
                fired.set()

        handler = _Handler()
        root = uia.GetRootElement()
        # TreeScope_Subtree = 0x7; cache request None = no caching.
        uia.AddStructureChangedEventHandler(root, 0x7, None, handler)
        self._uia, self._handler, self._root = uia, handler, root

    def _teardown(self) -> None:
        uia, handler, root = self._uia, self._handler, self._root
        self._uia = self._handler = self._root = None
        if uia is not None and handler is not None and root is not None:
            try:
                uia.RemoveStructureChangedEventHandler(root, handler)
            except Exception:
                pass

    def wait(self, timeout_s: float) -> bool:
        """Block until a structure change fires or the timeout elapses."""
        if not self.available:
            return False
        return self._fired.wait(timeout_s)

    def clear(self) -> None:
        self._fired.clear()

    def close(self) -> None:
        self._teardown()
        self.available = False


# ---------------------------------------------------------------------------
# World model
# ---------------------------------------------------------------------------


class WorldModel:
    """Thread-safe persistent model of the desktop.

    Usage::

        world = WorldModel()
        world.start()                    # background refresh begins
        frame = world.current_frame()    # latest frame, never blocks
        frame = world.wait_for_change(2.0)  # or None on timeout
        world.stop()
    """

    def __init__(self, poll_interval_s: float = 0.5) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        self._poll_interval_s = poll_interval_s
        self._lock = threading.Lock()
        # Placeholder so current_frame() is valid even before start().
        self._frame = ScreenFrame(
            seq=0,
            png=_placeholder_png(),
            width=1,
            height=1,
            elements={},
            changed_ids=set(),
        )
        self._seq = 0
        self._frame_event = threading.Event()  # set when a new frame publishes
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._uia_source: _UiaEventSource | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Begin background refresh. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        # A dead thread without stop() leaves a subscribed UIA source behind;
        # close it before replacing, or COM handlers accumulate.
        old_source, self._uia_source = self._uia_source, None
        if old_source is not None:
            old_source.close()
        self._stop_event.clear()
        self._frame_event.clear()
        self._uia_source = _UiaEventSource()
        # Synchronous first frame so current_frame() is meaningful immediately.
        try:
            self._refresh_once()
        except Exception:
            log.debug("initial world refresh failed", exc_info=True)
        self._thread = threading.Thread(
            target=self._run, name="v2-world", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop background refresh. Idempotent."""
        self._stop_event.set()
        source, self._uia_source = self._uia_source, None
        if source is not None:
            source.close()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    # -- refresh loop ---------------------------------------------------

    def _run(self) -> None:
        source = self._uia_source
        while not self._stop_event.is_set():
            if source is not None and source.available:
                source.wait(self._poll_interval_s)
                source.clear()
            else:
                self._stop_event.wait(self._poll_interval_s)
            if self._stop_event.is_set():
                break
            try:
                self._refresh_once()
            except Exception:
                log.debug("world refresh failed", exc_info=True)

    def _refresh_once(self) -> bool:
        """Capture, snapshot, diff; publish a frame iff the tree changed."""
        try:
            png, width, height = _capture_png()
        except Exception:
            log.debug("screenshot capture failed; using placeholder", exc_info=True)
            png, width, height = _placeholder_png(), 1, 1
        elements = _snapshot_tree()
        with self._lock:
            changed = _diff_elements(self._frame.elements, elements)
            # On platforms without UIA the tree is always empty; without the
            # screenshot check no frame would ever publish after the first
            # and consumers would verify against a frozen image.
            png_changed = png != self._frame.png
            if not changed and not png_changed and self._seq > 0:
                return False
            self._seq += 1
            self._frame = ScreenFrame(
                seq=self._seq,
                png=png,
                width=width,
                height=height,
                elements=elements,
                changed_ids=changed,
            )
            self._frame_event.set()
            return True

    # -- reads ----------------------------------------------------------

    def current_frame(self) -> ScreenFrame:
        """Latest published frame. Never blocks."""
        with self._lock:
            return self._frame

    def wait_for_change(self, timeout_s: float) -> ScreenFrame | None:
        """Block until a new (changed) frame publishes; None on timeout.

        Tracks the frame sequence rather than just the event flag, so a
        stale signal can never be mistaken for a new change. Written for a
        single consumer.
        """
        with self._lock:
            start_seq = self._frame.seq
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._frame_event.wait(remaining)
            with self._lock:
                frame = self._frame
                if frame.seq != start_seq:
                    return frame
                # Stale signal from an older publish. Clear it while holding
                # the lock so a publish racing this read can neither be
                # missed (a publish landing after the clear re-sets the
                # event and wakes the next wait) nor double-counted (a
                # publish landing before the clear shows up in the seq
                # check above).
                self._frame_event.clear()

    def element(self, element_id: str) -> Element | None:
        """Look up an element in the current frame. Never blocks."""
        with self._lock:
            return self._frame.elements.get(element_id)
