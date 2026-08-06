from pathlib import Path

import pytest


INSTALL_ENTRYPOINTS = ("scripts/install.ps1", "update.bat", "scripts/build_release.ps1")


def _requirement_lines() -> set[str]:
    return {
        line.split("#", 1)[0].strip().lower()
        for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }


def test_funasr_uses_published_dependency_metadata():
    """Install a current FunASR normally instead of copying its dependencies."""
    requirements = _requirement_lines()

    assert {
        "funasr>=1.3.28",
        "hydra-core>=1.3.2",
        "soundfile>=0.12.1",
    } <= requirements
    assert "editdistance-s>=1.0.0" not in requirements


@pytest.mark.parametrize("path", INSTALL_ENTRYPOINTS)
def test_install_entrypoint_resolves_funasr_dependencies(path: str):
    installer = Path(path).read_text(encoding="utf-8").lower()

    assert "-r requirements.txt" in installer
    assert "--no-deps" not in installer


def test_update_stops_when_dependency_installation_fails():
    updater = Path("update.bat").read_text(encoding="utf-8").lower()
    dependency_block = updater.split("install -r requirements.txt", 1)[1].split(
        "install pysbd", 1
    )[0]

    assert "exit /b 1" in dependency_block


def test_readmes_do_not_describe_the_removed_editdistance_workaround():
    for path in (Path("README.md"), Path("README_en.md")):
        text = path.read_text(encoding="utf-8").lower()
        assert "--no-deps" not in text
        assert "editdistance-s" not in text
