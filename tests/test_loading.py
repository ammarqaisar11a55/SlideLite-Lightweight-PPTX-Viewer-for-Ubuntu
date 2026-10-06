import hashlib
import json

import pytest

from slidelite.presentation import document, xmlsafe
from slidelite.presentation.document import RecoverablePackageError
from slidelite.presentation.opc import (
    EncryptedPackageError,
    LegacyFormatError,
    Package,
    PackageError,
    normalize_partname,
    resolve_target,
)
from slidelite.server.router import Router
from slidelite.server.session import DocumentSession, error_payload


def test_loads_slide_count_and_size(fixtures_dir):
    pres = document.load(fixtures_dir / "multi-slide.pptx")
    assert len(pres.slides) == 12
    assert pres.slides[0].partname == "/ppt/slides/slide1.xml"
    assert round(pres.width_pt) == 960 and round(pres.height_pt) == 540
    assert pres.kind == "presentation"
    pres.close()


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("aspect-4x3.pptx", (720, 540)),
        ("aspect-16x9.pptx", (960, 540)),
        ("aspect-custom.pptx", (595, 842)),
    ],
)
def test_aspect_ratios(fixtures_dir, name, size):
    pres = document.load(fixtures_dir / name)
    assert (round(pres.width_pt), round(pres.height_pt)) == size
    pres.close()


def test_source_file_is_never_modified(fixtures_dir):
    path = fixtures_dir / "multi-slide.pptx"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    mtime = path.stat().st_mtime_ns
    pres = document.load(path)
    session = DocumentSession(pres, str(path))
    for i in range(len(pres.slides)):
        session.render_slide(i)
    session.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert path.stat().st_mtime_ns == mtime


@pytest.mark.parametrize(
    "name",
    ["corrupt-truncated.pptx", "corrupt-random.pptx", "corrupt-empty.pptx", "hostile-zipbomb.pptx"],
)
def test_corrupt_files_raise_friendly_errors(fixtures_dir, name):
    with pytest.raises(PackageError) as exc:
        document.load(fixtures_dir / name)
    assert exc.value.message
    payload = error_payload(exc.value, name)
    assert payload["title"] and payload["message"]
    json.dumps(payload)


def test_encrypted_and_legacy_files(fixtures_dir):
    with pytest.raises(EncryptedPackageError):
        document.load(fixtures_dir / "encrypted.pptx")
    with pytest.raises(LegacyFormatError):
        document.load(fixtures_dir / "legacy.ppt")


def test_missing_files(tmp_path):
    with pytest.raises(PackageError):
        document.load(tmp_path / "nope.pptx")
    with pytest.raises(PackageError):
        document.load(tmp_path)


def test_missing_slide_is_recoverable(fixtures_dir):
    with pytest.raises(RecoverablePackageError) as exc:
        document.load(fixtures_dir / "corrupt-missing-slide.pptx")
    assert "slide2" in exc.value.detail
    assert error_payload(exc.value, "x")["recoverable"] is True
    pres = document.load(fixtures_dir / "corrupt-missing-slide.pptx", lenient=True)
    assert len(pres.slides) == 11
    assert pres.issues
    pres.close()


def test_ppsx_extension_opens(fixtures_dir):
    pres = document.load(fixtures_dir / "renamed-copy.ppsx")
    assert len(pres.slides) == 12
    pres.close()


def test_xxe_is_rejected(fixtures_dir):
    pres = document.load(fixtures_dir / "hostile-xxe.pptx")
    with pytest.raises(xmlsafe.XMLSecurityError):
        pres.package.xml("/ppt/slides/slide1.xml")
    pres.close()


def test_billion_laughs_is_rejected():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><r>&b;</r>'
    )
    with pytest.raises(xmlsafe.XMLSecurityError):
        xmlsafe.parse(bomb)


def test_path_traversal_relationship_is_dropped(fixtures_dir):
    pres = document.load(fixtures_dir / "hostile-traversal.pptx")
    rels = pres.package.rels("/ppt/slides/slide1.xml")
    assert "rIdX" not in rels
    pres.close()


def test_partname_helpers():
    assert normalize_partname("ppt/slides/slide1.xml") == "/ppt/slides/slide1.xml"
    assert (
        resolve_target("/ppt/slides/slide1.xml", "../media/image1.png") == "/ppt/media/image1.png"
    )
    assert resolve_target("/ppt/slides/slide1.xml", "/ppt/media/a%20b.png") == "/ppt/media/a b.png"
    with pytest.raises(PackageError):
        resolve_target("/ppt/slides/slide1.xml", "../../../x")
    with pytest.raises(PackageError):
        normalize_partname("../evil")


def test_case_insensitive_part_lookup(fixtures_dir):
    with Package(fixtures_dir / "multi-slide.pptx") as pkg:
        assert pkg.has("/PPT/Slides/Slide1.XML")
        assert pkg.content_type("/ppt/slides/slide1.xml").endswith("slide+xml")


def test_session_routes(fixtures_dir):
    router = Router()
    pres = document.load(fixtures_dir / "images.pptx")
    session = DocumentSession(pres, "images.pptx")
    session.mount(router)
    info = json.loads(router.handle(f"/doc/{session.id}/info.json").body)
    assert info["slideCount"] == 1 and round(info["width"]) == 960
    assert router.handle(f"/doc/{session.id}/slide/1.html").status == 200
    assert router.handle(f"/doc/{session.id}/slide/2.html").status == 404
    assert router.handle(f"/doc/{session.id}/slide/x.html").status == 404
    media = [p for p in pres.package.partnames() if p.startswith("/ppt/media/")]
    assert media
    resp = router.handle(f"/doc/{session.id}/part{media[0]}")
    assert resp.status == 200 and resp.mime == "image/png"
    # Only media parts are exposed, never XML or arbitrary parts.
    assert router.handle(f"/doc/{session.id}/part/ppt/presentation.xml").status == 404
    assert router.handle(f"/doc/{session.id}/part/../../etc/passwd").status == 404
    session.unmount(router)
    assert router.handle(f"/doc/{session.id}/info.json").status == 404
    session.close()


def test_alternate_content_prefers_fallback():
    root = xmlsafe.parse(
        b'<p:spTree xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        b'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        b'<mc:AlternateContent><mc:Choice Requires="a14"><p:sp id="choice"/></mc:Choice>'
        b'<mc:Fallback><p:pic id="fallback"/></mc:Fallback></mc:AlternateContent>'
        b'<p:sp id="x"/></p:spTree>'
    )
    xmlsafe.resolve_alternate_content(root)
    assert [el.get("id") for el in root] == ["fallback", "x"]
