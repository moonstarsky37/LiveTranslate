"""Platform-dispatching shim for overlay click-through (import path for the
overlay windows).

The backends live beside this module: win32.py (WS_EX_TRANSPARENT via
ctypes/user32, Windows) and macos.py (NSWindow.setIgnoresMouseEvents_ via
pyobjc). Both expose an idempotent set_click_through(winid, enabled); only the
macOS one is additionally total and null-safe, which it needs because it is
polled through native window rebuilds.
"""

import sys

if sys.platform == "darwin":
    from sublume.ui.overlay.macos import set_click_through
else:
    # Windows - and any other platform, which is unsupported: the win32 import
    # itself succeeds anywhere (ctypes is stdlib), but calls into windll fail.
    from sublume.ui.overlay.win32 import set_click_through

__all__ = ["set_click_through"]
