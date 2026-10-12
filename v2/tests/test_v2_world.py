"""Unit tests for v2.world — headless, no display, no network, no LLM.

Tree snapshots and screenshots are faked via monkeypatching the module-level
_capture_png / _snapshot_tree hooks, except for the graceful-degradation
test which exercises the real (empty-tree) path on non-Windows.
"""

from __future__ import annotations

import dataclasses
import sys
import threading
import time

import pytest

import v2.world as world_mod
from v2.world import Element, ScreenFrame, WorldModel, _diff_elements


def _el(
    eid: str,
    *,
    rect: tuple[int, int, int, int] = (0, 0, 10, 10),
    name: str = "n",
    value: str | None = None,
    control_type: str = "Button",
) -> Element:
    return Element(
        id=eid,
        control_type=control_type,
        name=name,
        rect=rect,
        enabled=True,
        visible=True,
        value=value,
    )


def _fake_png() -> tuple[bytes, int, int]:
    return world_mod._placeholder_png(), 1, 1


# ---------------------------------------------------------------------------
# diffing
# ---------------------------------------------------------------------------


def test_diff_detects_added_removed_rect_and_value_changes():
    old = {
        "a": _el("a"),
        "b": _el("b", rect=(0, 0, 5, 5)),
        "d": _el("d", value=None),
        "gone": _el("gone"),
    }
    new = {
        "a": _el("a"),
        "b": _el("b", rect=(1, 1, 5, 5)),  # rect changed
        "c": _el("c"),  # added
        "d": _el("d", value="typed"),  # value changed
    }
    assert _diff_elements(old, new) == {"b", "c", "d", "gone"}


def test_diff_detects_name_and_flag_changes():
    old = {"a": _el("a", name="Save")}
    assert _diff_elements(old, {"a": _el("a", name="Save As")}) == {"a"}
    disabled = dataclasses.replace(old["a"], enabled=False)
    assert _diff_elements(old, {"a": disabled}) == {"a"}


def test_diff_empty_when_identical():
    old = {"a": _el("a"), "b": _el("b", value="x")}
    assert _diff_elements(old, dict(old)) == set()
    assert _diff_elements({}, {}) == set()


def test_element_is_frozen():
    el = _el("a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        el.name = "b"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# frame publishing + id stability (synthetic trees)
# ---------------------------------------------------------------------------


def test_id_stability_and_pairwise_publish(monkeypatch):
    trees = [
        {"100:1.2": _el("100:1.2", name="Save")},
        {
            "100:1.2": _el("100:1.2", name="Save"),
            "100:1.3": _el("100:1.3", name="Cancel"),
        },
    ]
    calls = {"n": 0}

    def fake_tree():
        i = min(calls["n"], len(trees) - 1)
        calls["n"] += 1
        return dict(trees[i])

    monkeypatch.setattr(world_mod, "_snapshot_tree", fake_tree)
    monkeypatch.setattr(world_mod, "_capture_png", _fake_png)

    wm = WorldModel(poll_interval_s=60)
    assert wm._refresh_once() is True
    f1 = wm.current_frame()
    assert set(f1.elements) == {"100:1.2"}
    assert f1.changed_ids == {"100:1.2"}  # everything is "added" on frame 1

    assert wm._refresh_once() is True
    f2 = wm.current_frame()
    assert f2.seq == f1.seq + 1
    # Stable identity: the surviving element keeps its id and compares equal.
    assert f2.elements["100:1.2"] == f1.elements["100:1.2"]
    assert f2.changed_ids == {"100:1.3"}

    # Identical tree -> no publish.
    assert wm._refresh_once() is False
    assert wm.current_frame().seq == f2.seq


def test_removed_element_reported(monkeypatch):
    trees = [{"a": _el("a"), "b": _el("b")}, {"a": _el("a")}]
    calls = {"n": 0}

    def fake_tree():
        i = min(calls["n"], 1)
        calls["n"] += 1
        return dict(trees[i])

    monkeypatch.setattr(world_mod, "_snapshot_tree", fake_tree)
    monkeypatch.setattr(world_mod, "_capture_png", _fake_png)

    wm = WorldModel(poll_interval_s=60)
    wm._refresh_once()
    wm._refresh_once()
    assert wm.current_frame().changed_ids == {"b"}
    assert wm.element("b") is None
    assert wm.element("a") is not None


def test_png_encoded_once_per_frame(monkeypatch):
    seen: list[bytes] = []

    def fake_capture():
        png, w, h = _fake_png()
        seen.append(png)
        return png, w, h

    monkeypatch.setattr(world_mod, "_capture_png", fake_capture)
    monkeypatch.setattr(world_mod, "_snapshot_tree", lambda: {"a": _el("a")})

    wm = WorldModel(poll_interval_s=60)
    wm._refresh_once()
    assert len(seen) == 1
    assert wm.current_frame().png is seen[0]  # stored, not re-encoded


# ---------------------------------------------------------------------------
# wait_for_change signaling
# ---------------------------------------------------------------------------


def test_wait_for_change_signals_on_tree_change(monkeypatch):
    gate = threading.Event()
    tree_a = {"a": _el("a")}
    tree_b = {"a": _el("a"), "b": _el("b")}

    monkeypatch.setattr(
        world_mod, "_snapshot_tree", lambda: dict(tree_b if gate.is_set() else tree_a)
    )
    monkeypatch.setattr(world_mod, "_capture_png", _fake_png)

    wm = WorldModel(poll_interval_s=0.02)
    wm.start()
    try:
        time.sleep(0.1)  # let it settle on tree_a (no changes pending)
        gate.set()
        frame = wm.wait_for_change(5.0)
        assert frame is not None
        assert "b" in frame.changed_ids
        assert frame.elements["b"].name == "n"
    finally:
        wm.stop()


def test_wait_for_change_timeout_when_static(monkeypatch):
    monkeypatch.setattr(world_mod, "_snapshot_tree", lambda: {"a": _el("a")})
    monkeypatch.setattr(world_mod, "_capture_png", _fake_png)

    wm = WorldModel(poll_interval_s=0.02)
    wm.start()
    try:
        assert wm.wait_for_change(0.2) is None
    finally:
        wm.stop()


def test_wait_for_change_never_misses_racing_publish(monkeypatch):
    """Regression: a frame published between the waiter's stale-signal read
    and its event clear must wake the waiter promptly (no lost wakeup).

    A lock wrapper publishes one frame when the waiter's seq-read block
    releases (the 2nd lock release) — the exact interleaving where a frame
    used to land between the read and the event clear, wiping its signal
    and leaving the waiter asleep until the full timeout.
    """
    monkeypatch.setattr(world_mod, "_snapshot_tree", lambda: {})
    monkeypatch.setattr(world_mod, "_capture_png", _fake_png)

    wm = WorldModel(poll_interval_s=3600)  # no background publishes
    wm.start()
    real_lock = wm._lock
    try:
        start_seq = wm.current_frame().seq
        wm._frame_event.clear()  # drain the initial publish signal
        wm._frame_event.set()    # stale signal: event set, seq unchanged

        def publish_frame():
            png, w, h = _fake_png()
            wm._seq += 1
            wm._frame = ScreenFrame(
                seq=wm._seq, png=png, width=w, height=h,
                elements={}, changed_ids={"x"},
            )
            wm._frame_event.set()

        published = {"releases": 0}

        class _RacingLock:
            """Publishes one frame when the waiter's seq-read block
            releases (the 2nd lock release)."""
            def __enter__(self):
                return real_lock.__enter__()

            def __exit__(self, *exc):
                try:
                    published["releases"] += 1
                    if published["releases"] == 2:
                        publish_frame()
                finally:
                    return real_lock.__exit__(*exc)

        wm._lock = _RacingLock()
        t0 = time.monotonic()
        frame = wm.wait_for_change(2.0)
        elapsed = time.monotonic() - t0
        assert frame is not None, "waiter timed out entirely"
        assert frame.seq == start_seq + 1
        assert "x" in frame.changed_ids
        # The racing frame must wake the waiter — not leave it asleep
        # until the timeout expires (the lost-wakeup symptom).
        assert elapsed < 1.0, f"waiter slept {elapsed:.2f}s on a live frame"
    finally:
        wm._lock = real_lock
        wm.stop()


def test_wait_for_change_returns_zero_timeout_immediately(monkeypatch):
    monkeypatch.setattr(world_mod, "_snapshot_tree", lambda: {"a": _el("a")})
    monkeypatch.setattr(world_mod, "_capture_png", _fake_png)
    wm = WorldModel(poll_interval_s=60)
    wm.start()
    try:
        assert wm.wait_for_change(0) is None
    finally:
        wm.stop()


# ---------------------------------------------------------------------------
# lifecycle / threading
# ---------------------------------------------------------------------------


def test_start_stop_idempotent():
    wm = WorldModel(poll_interval_s=60)
    wm.start()
    wm.start()  # second start must not spawn another thread
    wm.stop()
    wm.stop()  # must not raise


def test_current_frame_usable_before_start():
    wm = WorldModel()
    frame = wm.current_frame()
    assert isinstance(frame, ScreenFrame)
    assert frame.seq == 0
    assert frame.png  # placeholder bytes
    assert wm.element("anything") is None


def test_current_frame_threadsafe_under_refresh(monkeypatch):
    gate = threading.Event()
    counter = {"n": 0}

    def fake_tree():
        counter["n"] += 1
        if gate.is_set():
            return {f"k{i}": _el(f"k{i}") for i in range(counter["n"] % 7)}
        return {"a": _el("a")}

    monkeypatch.setattr(world_mod, "_snapshot_tree", fake_tree)
    monkeypatch.setattr(world_mod, "_capture_png", _fake_png)

    wm = WorldModel(poll_interval_s=0.01)
    wm.start()
    errors: list[BaseException] = []

    def hammer():
        try:
            for _ in range(200):
                f = wm.current_frame()
                assert isinstance(f.seq, int)
                wm.element("a")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    gate.set()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wm.stop()
    assert not errors


def test_capture_failure_degrades_to_placeholder(monkeypatch):
    def boom():
        raise RuntimeError("no display")

    monkeypatch.setattr(world_mod, "_capture_png", boom)
    monkeypatch.setattr(world_mod, "_snapshot_tree", lambda: {"a": _el("a")})
    wm = WorldModel(poll_interval_s=60)
    assert wm._refresh_once() is True
    frame = wm.current_frame()
    assert frame.png  # placeholder, loop keeps running
    assert frame.width == 1 and frame.height == 1


# ---------------------------------------------------------------------------
# graceful degradation on non-Windows (this Linux box)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behavior")
def test_empty_tree_graceful_on_linux():
    # Real code path: no monkeypatching. UIA unavailable -> empty tree,
    # screenshot attempt fails headless -> placeholder. Everything works.
    wm = WorldModel(poll_interval_s=0.05)
    wm.start()
    try:
        time.sleep(0.2)  # a few real poll cycles
        frame = wm.current_frame()
        assert frame.elements == {}
        assert frame.changed_ids == set()
        assert wm.element("123:4.5") is None
        assert frame.png and frame.width >= 1 and frame.height >= 1
        assert wm.wait_for_change(0.15) is None  # nothing ever changes
    finally:
        wm.stop()
