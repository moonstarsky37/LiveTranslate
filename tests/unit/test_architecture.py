"""Architectural regression tests.

Rule: designated modules must be importable without Qt or torch — that is
what makes them unit-testable anywhere (including Linux CI) and is the
foundation for the macOS port. A plain import check is NOT enough
on dev machines where the project venv has PyQt6/torch installed, so each
module is imported in a subprocess with a sys.meta_path blocker that raises
on any Qt/torch import at any depth.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Extend as Phase 3 purifies more modules (core.vad_processor is torch-bound by
# nature; core.audio_capture is Windows-bound until the capture interface lands).
QT_TORCH_FREE_MODULES = (
    "sublume.core.pipeline",
    "sublume.core.segmentation",
    "sublume.config.schema",
    "sublume.config.store",
    "sublume.paths",
    "sublume.asr.mem_policy",
    "sublume.ui.overlay.click_through",
    "sublume.ui.overlay.macos",
)

_BLOCKER = """
import sys

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("PyQt6", "PyQt5", "torch"):
            raise AssertionError("FORBIDDEN module-level import: " + name)
        return None

sys.meta_path.insert(0, Blocker())
import {module}
print("OK")
"""


@pytest.mark.parametrize("module", QT_TORCH_FREE_MODULES)
def test_module_imports_without_qt_or_torch(module):
    result = subprocess.run(
        [sys.executable, "-c", _BLOCKER.format(module=module)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{module} pulled in Qt/torch at import time:\n{result.stderr}"
    )
