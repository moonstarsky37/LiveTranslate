"""Click-through backend split: platform dispatch plus the macOS backend's
idempotency, early-outs and total-function contract — all runnable anywhere
by injecting a fake pyobjc (macos.py imports it lazily).
"""

import ctypes
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import sublume.ui.overlay.macos as mac  # noqa: E402


class _FakeWindow:
    """NSWindow stand-in: ignoresMouseEvents() reports the PRE-state so the
    idempotency case has something to observe."""

    def __init__(self, state):
        self._state = state
        self.calls = []

    def ignoresMouseEvents(self):
        return self._state

    def setIgnoresMouseEvents_(self, value):
        self.calls.append(value)
        self._state = value


class _FakeView:
    def __init__(self, window):
        self._window = window

    def window(self):
        return self._window


def _install_fake_objc(monkeypatch, view, *, seen=None, boom=False):
    """Inject a fake objc AND a placeholder AppKit.

    Both are required: macos._objc() does `import AppKit` before `import objc`,
    so injecting only objc makes that import raise and every case silently
    lands in the except arm, testing nothing.
    """

    def objc_object(*, c_void_p):  # keyword-only, mirroring the real call site
        if seen is not None:
            seen.append(c_void_p)
        if boom:
            raise RuntimeError("simulated pyobjc failure")
        return view

    fake = types.SimpleNamespace(objc_object=objc_object)
    monkeypatch.setitem(sys.modules, "objc", fake)
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace())
    return fake


# ── (a) dispatch ──


def test_shim_dispatches_by_platform():
    """The shim resolves to this platform's backend. Each CI runner covers one
    side; the mac job's assertion is the only guard on the darwin import line."""
    from sublume.ui.overlay import click_through

    if sys.platform == "darwin":
        assert click_through.set_click_through is mac.set_click_through
    else:
        import sublume.ui.overlay.win32 as win32

        assert click_through.set_click_through is win32.set_click_through


# ── (b) macOS backend against a fake pyobjc ──


def test_sets_the_flag_once_when_the_state_differs(monkeypatch):
    monkeypatch.setattr(mac, "_warned", False)
    window = _FakeWindow(False)
    _install_fake_objc(monkeypatch, _FakeView(window))

    mac.set_click_through(0x1000, True)

    assert window.calls == [True]
    assert mac._warned is False


def test_clearing_the_flag_passes_false(monkeypatch):
    monkeypatch.setattr(mac, "_warned", False)
    window = _FakeWindow(True)
    _install_fake_objc(monkeypatch, _FakeView(window))

    mac.set_click_through(0x1000, False)

    assert window.calls == [False]
    assert mac._warned is False


def test_is_idempotent_when_the_state_already_matches(monkeypatch):
    """The overlay polls this at 20 Hz — a no-op must not message the window."""
    monkeypatch.setattr(mac, "_warned", False)
    on = _FakeWindow(True)
    _install_fake_objc(monkeypatch, _FakeView(on))
    mac.set_click_through(0x1000, True)
    assert on.calls == []

    off = _FakeWindow(False)
    _install_fake_objc(monkeypatch, _FakeView(off))
    mac.set_click_through(0x1000, False)
    assert off.calls == []
    assert mac._warned is False


def test_null_view_id_never_reaches_objc(monkeypatch):
    """winId() is 0 before the native window exists; _warned staying False
    proves the early-out ran instead of the exploding fake."""
    monkeypatch.setattr(mac, "_warned", False)
    seen = []
    _install_fake_objc(monkeypatch, None, seen=seen, boom=True)

    mac.set_click_through(0, True)

    assert seen == []
    assert mac._warned is False


def test_missing_view_is_a_silent_no_op(monkeypatch):
    monkeypatch.setattr(mac, "_warned", False)
    _install_fake_objc(monkeypatch, None)

    mac.set_click_through(0x1000, True)

    assert mac._warned is False


def test_view_without_a_window_is_a_silent_no_op(monkeypatch):
    """setWindowFlags() rebuilds the native window; a poll tick can land in
    the gap where the view is not attached yet."""
    monkeypatch.setattr(mac, "_warned", False)
    _install_fake_objc(monkeypatch, _FakeView(None))

    mac.set_click_through(0x1000, True)

    assert mac._warned is False


def test_view_id_is_wrapped_in_c_void_p(monkeypatch):
    """A bare int into objc_object(c_void_p=...) is untested territory, so the
    backend always wraps explicitly; the mac CI job checks the real roundtrip."""
    monkeypatch.setattr(mac, "_warned", False)
    seen = []
    _install_fake_objc(monkeypatch, _FakeView(_FakeWindow(False)), seen=seen)

    mac.set_click_through(0xDEADBEEF, True)

    assert len(seen) == 1
    assert isinstance(seen[0], ctypes.c_void_p)
    assert seen[0].value == 0xDEADBEEF


def test_appkit_import_failure_is_caught_and_logged_once(monkeypatch, caplog):
    """A None entry in sys.modules makes `import AppKit` raise
    ModuleNotFoundError from inside _objc(), so the import really executes and
    the except arm really catches it. Kills three mutants: dropping the AppKit
    import, hoisting the _objc() call above the try, and narrowing the except
    off Exception. (objc is deliberately NOT faked - AppKit is imported first,
    so the bogus view id never reaches objc_object.)"""
    monkeypatch.setattr(mac, "_warned", False)
    monkeypatch.setitem(sys.modules, "AppKit", None)

    with caplog.at_level(logging.WARNING, logger="Sublume.UI"):
        mac.set_click_through(1234, True)

    assert mac._warned is True
    assert len([r for r in caplog.records if r.name == "Sublume.UI"]) == 1


def test_backend_failure_is_swallowed_and_logged_once(monkeypatch, caplog):
    """Total function: the 20 Hz poll site calls it unguarded, and a failing
    backend must not spam the log either."""
    monkeypatch.setattr(mac, "_warned", False)
    seen = []
    _install_fake_objc(monkeypatch, None, seen=seen, boom=True)

    with caplog.at_level(logging.WARNING, logger="Sublume.UI"):
        mac.set_click_through(0x1000, True)
        mac.set_click_through(0x1000, False)

    assert len(seen) == 2  # the backend really was reached both times
    assert len([r for r in caplog.records if r.name == "Sublume.UI"]) == 1
    assert mac._warned is True
