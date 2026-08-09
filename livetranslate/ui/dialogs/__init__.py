"""Dialog package — split from the former single-file ui/dialogs.py (M3-2).

Pure re-exports: every import path that worked against the old module keeps
working against this package. New code may import from the concrete
submodule directly.

    _common.py         shared logger + log/stderr capture shims
    model_load.py      _ModelLoadDialog (engine-switch loading window)
    setup_wizard.py    SetupWizardDialog (first launch)
    model_download.py  ModelDownloadDialog (missing models)
    model_edit.py      ModelEditDialog (translation model form)

The changelog helpers that used to sit at the bottom of the old module are
not dialogs at all; they now live in livetranslate.ui.changelog.
"""

from livetranslate.ui.dialogs._common import (
    _LogCapture,
    _short_error,
    _StderrCapture,
)
from livetranslate.ui.dialogs.model_download import ModelDownloadDialog
from livetranslate.ui.dialogs.model_edit import ModelEditDialog
from livetranslate.ui.dialogs.model_load import _ModelLoadDialog
from livetranslate.ui.dialogs.setup_wizard import SetupWizardDialog

__all__ = [
    "ModelDownloadDialog",
    "ModelEditDialog",
    "SetupWizardDialog",
    "_LogCapture",
    "_ModelLoadDialog",
    "_StderrCapture",
    "_short_error",
]
