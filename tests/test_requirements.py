from pathlib import Path

import pytest


INSTALL_ENTRYPOINTS = ("scripts/install.ps1", "update.bat", "scripts/build_release.ps1")


def _requirement_lines(filename: str) -> set[str]:
    return {
        line.split("#", 1)[0].strip().lower()
        for line in Path(filename).read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }


def test_funasr_stack_lives_in_the_torch_profile():
    """funasr installs from its published metadata (never a hand-copied
    dependency list with --no-deps), and since the profile split the whole
    stack sits in requirements-torch.txt - the base profile stays torch-free."""
    torch_reqs = _requirement_lines("requirements-torch.txt")
    assert {
        "funasr>=1.3.28",
        "hydra-core>=1.3.2",
        "soundfile>=0.12.1",
    } <= torch_reqs
    assert "editdistance-s>=1.0.0" not in torch_reqs

    base = _requirement_lines("requirements.txt")
    assert not any(line.startswith(("funasr", "torch", "silero-vad")) for line in base)
    assert any(line.startswith("onnxruntime") for line in base)


def test_base_numpy_cap_guards_the_torch_profile_resolver():
    """numba (torch profile) has no wheels for numpy>=2.3; the cap must live
    in base because base is installed first and decides the numpy version
    for both profiles (idea harvested from upstream #40)."""
    base = _requirement_lines("requirements.txt")
    assert any(line.startswith("numpy") and "<2.3" in line for line in base)


@pytest.mark.parametrize("path", INSTALL_ENTRYPOINTS)
def test_install_entrypoint_covers_both_profiles(path: str):
    installer = Path(path).read_text(encoding="utf-8").lower()
    assert "-r requirements.txt" in installer
    assert "-r requirements-torch.txt" in installer
    assert "--no-deps" not in installer


def test_update_stops_when_dependency_installation_fails():
    updater = Path("update.bat").read_text(encoding="utf-8").lower()
    dependency_block = updater.split("install -r requirements.txt", 1)[1]
    assert "exit /b 1" in dependency_block


def test_readmes_do_not_describe_the_removed_editdistance_workaround():
    for path in (Path("README.md"), Path("README_en.md")):
        text = path.read_text(encoding="utf-8").lower()
        assert "--no-deps" not in text
        assert "editdistance-s" not in text
