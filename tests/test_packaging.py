"""The .deb package: contents, file modes, metadata and a working launcher."""

import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import slidelite

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.ammarqaisar.SlideLite"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not available"),
]


@pytest.fixture(scope="module")
def deb(tmp_path_factory):
    out = tmp_path_factory.mktemp("deb")
    result = subprocess.run(
        ["bash", str(ROOT / "packaging" / "build-deb.sh"), str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    path = Path(result.stdout.strip().splitlines()[-1])
    assert path.name == f"slidelite_{slidelite.__version__}_all.deb"
    return path


@pytest.fixture(scope="module")
def tree(deb, tmp_path_factory):
    root = tmp_path_factory.mktemp("root")
    subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    return root


def test_control_metadata(deb):
    info = subprocess.run(
        ["dpkg-deb", "-f", str(deb)], capture_output=True, text=True, check=True
    ).stdout
    assert "Package: slidelite" in info
    assert f"Version: {slidelite.__version__}" in info
    assert "Architecture: all" in info
    for dep in ("python3-gi", "gir1.2-gtk-3.0", "gir1.2-webkit2-4.1"):
        assert dep in info


def test_installed_files(tree):
    expected = [
        "usr/bin/slidelite",
        f"usr/share/applications/{APP_ID}.desktop",
        f"usr/share/metainfo/{APP_ID}.metainfo.xml",
        f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg",
        "usr/share/man/man1/slidelite.1.gz",
        "usr/share/doc/slidelite/copyright",
        "usr/share/slidelite/slidelite/cli.py",
        "usr/share/slidelite/slidelite/data/presets.json",
        "usr/share/slidelite/slidelite/web/index.html",
    ]
    for rel in expected:
        assert (tree / rel).is_file(), rel
    assert not list(tree.rglob("__pycache__"))
    assert not list(tree.rglob("tests"))


def test_file_modes(deb):
    with tarfile.open(fileobj=_data_tar(deb)) as data:
        for member in data.getmembers():
            mode = stat.S_IMODE(member.mode)
            assert not mode & stat.S_IWOTH, member.name
            assert member.uid == 0 and member.gid == 0, member.name
            if member.name == "./usr/bin/slidelite":
                assert mode == 0o755


def test_desktop_entry_associates_pptx(tree):
    entry = (tree / f"usr/share/applications/{APP_ID}.desktop").read_text()
    assert "application/vnd.openxmlformats-officedocument.presentationml.presentation;" in entry
    assert "Exec=slidelite %U" in entry
    if shutil.which("desktop-file-validate"):
        subprocess.run(
            ["desktop-file-validate", str(tree / f"usr/share/applications/{APP_ID}.desktop")],
            check=True,
        )


def test_launcher_runs_from_installed_layout(tree):
    launcher = (tree / "usr/bin/slidelite").read_text()
    assert launcher.startswith("#!/usr/bin/python3")
    patched = launcher.replace("/usr/share/slidelite", str(tree / "usr/share/slidelite"))
    result = subprocess.run(
        [sys.executable, "-c", patched, "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert f"SlideLite {slidelite.__version__}" in result.stdout


def _data_tar(deb: Path):
    import io

    # A .deb is an ar archive: debian-binary, control.tar.*, data.tar.*
    raw = deb.read_bytes()
    assert raw.startswith(b"!<arch>\n")
    pos = 8
    while pos < len(raw):
        name = raw[pos : pos + 16].decode().strip()
        size = int(raw[pos + 48 : pos + 58].decode().strip())
        body = raw[pos + 60 : pos + 60 + size]
        if name.startswith("data.tar"):
            return io.BytesIO(body)
        pos += 60 + size + (size & 1)
    raise AssertionError("no data.tar member")
