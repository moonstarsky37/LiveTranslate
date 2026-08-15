"""macOS click-through helper: whole-window NSWindow.setIgnoresMouseEvents_.

pyobjc is imported lazily so importing this module is safe on any platform
(the CI import smoke runs it on Windows too) and tests can inject fakes
before the first call.
"""

import ctypes
import logging

log = logging.getLogger("Sublume.UI")

# Process-wide: the caller polls at 20 Hz, so the failure warning is logged once.
_warned = False


def _objc():
    # Lazy so importing this module is safe on any platform and tests can
    # inject fakes before the first call (mirrors core/capture/macos._sd).
    import AppKit  # noqa: F401 - ensures the AppKit framework + pyobjc NSWindow metadata are loaded before messaging an NSWindow; do not autofix away
    import objc

    return objc


def set_click_through(view_id: int, enabled: bool) -> None:
    """Idempotently set/clear ignoresMouseEvents on the NSWindow owning the
    Qt window's NSView (winId() on macOS is an NSView pointer).

    Total: never raises - the overlay polls this at 20 Hz from an unguarded Qt
    slot, and setWindowFlags() tears down and recreates the native view
    mid-flight, so every step is guarded and the id is re-fetched per call.
    The NSWindow is intentionally never cached: after a native rebuild the old
    address may be reused, and messaging a cached stale pointer is a hard crash
    (EXC_BAD_ACCESS), not a catchable exception.
    """
    global _warned
    try:
        if not view_id:
            return
        objc = _objc()
        view = objc.objc_object(c_void_p=ctypes.c_void_p(view_id))
        # Insurance / test seam: real pyobjc hands back a proxy (or crashes)
        # for a non-null pointer and never None, but the guard costs nothing.
        if view is None:
            return
        window = view.window()
        if window is None:
            return
        if bool(window.ignoresMouseEvents()) != bool(enabled):
            window.setIgnoresMouseEvents_(bool(enabled))
    except Exception:
        if not _warned:
            _warned = True
            log.warning("macOS click-through unavailable", exc_info=True)
