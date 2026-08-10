"""Worker ready payload: the device label must report what the engine actually
loaded on, not what the config asked for, and reach the client that reads it.

With a CPU-only torch build a "cuda" request silently falls back to CPU, and
the log/monitor bar would keep claiming "funasr on cuda". Qt/torch-free: the
payload helper is pure dict work over an already-built engine object, and the
client side is driven over a fake pipe with no worker process.
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sublume.asr.client import ASRClient  # noqa: E402
from sublume.asr.worker import _ready_payload  # noqa: E402


def _config(device="cuda"):
    return {
        "engine_type": "funasr",
        "display_name": "FunASR SenseVoice",
        "device": device,
        "model_size": "large-v3",
    }


def test_real_fallback_is_reported():
    engine = types.SimpleNamespace(device="cpu")
    assert _ready_payload(engine, _config("cuda"))["device"] == "cpu"


def test_engine_without_a_device_attribute_keeps_the_config_value():
    engine = types.SimpleNamespace()
    assert _ready_payload(engine, _config("cuda"))["device"] == "cuda"


def test_engine_reporting_none_keeps_the_config_value():
    # funasr engines return None while unloaded (self._model is None).
    engine = types.SimpleNamespace(device=None)
    assert _ready_payload(engine, _config("cuda"))["device"] == "cuda"


def test_same_device_at_a_different_granularity_keeps_the_config_format():
    # config.yaml ships bare "cuda"; whisper/anime build "cuda:0". Same device,
    # so do not churn the label format - only a real fallback may change it.
    engine = types.SimpleNamespace(device="cuda:0")
    assert _ready_payload(engine, _config("cuda"))["device"] == "cuda"


class _RaisingDevice:
    """A device property that blows up with something getattr will NOT swallow."""

    @property
    def device(self):
        raise RuntimeError("engine exploded while reporting its device")


def test_a_raising_device_property_does_not_break_a_loaded_engine():
    # The engine loaded fine; a broken label must not turn that into a failure.
    assert _ready_payload(_RaisingDevice(), _config("cuda"))["device"] == "cuda"


def test_an_unparsable_device_string_keeps_the_config_value():
    # _parse_device does int() on the index and raises ValueError here.
    engine = types.SimpleNamespace(device="cuda:x")
    assert _ready_payload(engine, _config("cuda"))["device"] == "cuda"


def test_payload_field_set_is_unchanged():
    engine = types.SimpleNamespace(device="cpu")
    payload = _ready_payload(engine, _config("cuda"))
    assert set(payload) == {"engine_type", "display_name", "device"}
    assert payload["engine_type"] == "funasr"
    assert payload["display_name"] == "FunASR SenseVoice"


def test_wait_ready_keeps_the_payload_for_the_label_resolver():
    # Real wait_ready over a fake pipe: no worker process is ever started.
    payload = {"device": "cpu", "engine_type": "x", "display_name": "y"}
    client = ASRClient(_config("cuda"))
    client._conn = types.SimpleNamespace(
        poll=lambda _timeout: True,
        recv=lambda: {"ok": True, "type": "ready", "payload": payload},
    )
    client._process = types.SimpleNamespace(pid=4321, exitcode=None)
    assert client.wait_ready(timeout=1.0) == payload
    assert client.ready_info["device"] == "cpu"
