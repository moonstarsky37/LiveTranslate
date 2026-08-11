"""Memory ceiling policy: how much combined RSS is "too much", per ASR engine.

The supervisor warns once when combined RSS (main + ASR worker) exceeds the
threshold for the engine currently loaded. The ASR backend runs in a worker
process and keeps native-side workspaces/caches that Python GC cannot always
reclaim, so the ceiling must include the worker's RSS (see
ASRSupervisor._mem_snapshot).

The 4096MB default was tuned in the funasr era. anime-whisper's worker alone
normally sits at 4.2-4.7GB right after load, so a flat default fires on every
session before anything is wrong. Its ceiling is raised to 8192MB, derived
from the worker recycle trigger (post-load baseline + _asr_recycle_delta_mb =
2048MB, so the worker tops out around 6.7GB) plus roughly 1.4GB of headroom
for the main process. Caveat on GPU machines: the main process carries torch
plus a CUDA context and can eat into that headroom, and the 30s tick may
sample a pre-recycle peak, so a single warning is still possible.

Qt/torch-free by design: it is plain data, read from the supervisor and
testable anywhere (see tests/unit/test_architecture.py).
"""

MEM_THRESHOLD_DEFAULT_MB = 4096

MEM_THRESHOLD_OVERRIDES_MB: dict[str, int] = {"anime-whisper": 8192}


def mem_threshold_for(asr_type: str | None) -> int:
    """Combined-RSS ceiling in MB for the given engine (canonical _asr_type)."""
    if not asr_type:
        return MEM_THRESHOLD_DEFAULT_MB
    return MEM_THRESHOLD_OVERRIDES_MB.get(asr_type, MEM_THRESHOLD_DEFAULT_MB)
