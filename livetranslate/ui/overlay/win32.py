"""Win32 extended-window-style helpers shared by the overlay windows."""

import ctypes

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x20


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Idempotently set/clear WS_EX_TRANSPARENT on a Win32 window.

    A window with WS_EX_TRANSPARENT passes mouse clicks to whatever is behind
    it, so it never blocks the video player / browser underneath.
    """
    style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if enabled:
        if not (style & _WS_EX_TRANSPARENT):
            ctypes.windll.user32.SetWindowLongW(
                hwnd, _GWL_EXSTYLE, style | _WS_EX_TRANSPARENT
            )
    elif style & _WS_EX_TRANSPARENT:
        ctypes.windll.user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE, style & ~_WS_EX_TRANSPARENT
        )
