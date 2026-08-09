"""Shared plumbing for the dialog package: the dialogs' logger, an error
shortener, and the two capture shims that mirror log / stderr (tqdm) output
into a dialog's live text view."""

import logging
import re

log = logging.getLogger("LiveTranslate.Dialogs")


def _short_error(exc: BaseException) -> str:
    """First line of an exception message, capped — the download dialogs show
    this to the user, so the multi-line CDN/CAS error dumps stay in the log."""
    text = str(exc).strip() or type(exc).__name__
    return text.splitlines()[0][:200]


class _LogCapture(logging.Handler):
    """Captures log output and emits via callback."""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            self._callback(self.format(record))
        except Exception:
            pass


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class _StderrCapture:
    """Captures stderr (tqdm) and forwards cleaned lines via callback."""

    def __init__(self, callback, original):
        self._cb = callback
        self._orig = original

    def write(self, text):
        if self._orig:
            self._orig.write(text)
        if not text:
            return
        cleaned = _ANSI_RE.sub("", text)
        for line in cleaned.splitlines():
            line = line.strip()
            if line:
                self._cb(line)

    def flush(self):
        if self._orig:
            self._orig.flush()

    def isatty(self):
        return False
