"""Shared Qt test hygiene.

Every widget fixture in this suite tears widgets down with
``close()`` + ``deleteLater()`` + ``processEvents()``. On this stack
(PyQt6 6.7.1, offscreen platform) ``processEvents()`` does not deliver
``QEvent.Type.DeferredDelete`` -- only a running event loop or an
explicit ``sendPostedEvents`` flush does, as proven by direct probe:
a plain widget survived repeated ``processEvents()`` calls but died
under ``sendPostedEvents(widget, DeferredDelete)``.

Without the flush, each DashboardWindow construction leaks its ~303
widgets into the next test. By the time the suite reaches the desktop
lifecycle tests, ``QApplication.allWidgets()`` holds 10,000+ leaked
widgets, and every ``app.setStyleSheet`` repolish over that pile takes
minutes -- the full suite grinds to what looks like a hang.

This autouse fixture flushes every pending deferred deletion after
each test, so the fixtures' ``deleteLater()`` calls actually take
effect. It is a no-op for tests that never created a QApplication.
"""

import pytest


@pytest.fixture(autouse=True)
def _flush_qt_deferred_deletions():
    yield
    try:
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    # Flush every pending deferred deletion, not just one widget's:
    # DashboardWindow's conversation refresh detaches cleared widgets
    # with setParent(None) + deleteLater(), so those orphans are only
    # reclaimed by the global flush.
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
