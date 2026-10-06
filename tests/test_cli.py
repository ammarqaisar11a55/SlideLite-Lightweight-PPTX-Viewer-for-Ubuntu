import os

import pytest

from slidelite.cli import parse_args


def test_no_arguments_opens_empty_viewer():
    opts = parse_args([])
    assert opts.files == ()
    assert not opts.fullscreen and not opts.presentation


def test_file_is_made_absolute():
    opts = parse_args(["deck.pptx"])
    assert opts.files == (os.path.abspath("deck.pptx"),)


def test_presentation_and_fullscreen_flags():
    assert parse_args(["--presentation", "a.pptx"]).presentation
    assert parse_args(["--fullscreen", "a.pptx"]).fullscreen


def test_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--fullscreen", "--presentation", "a.pptx"])


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--version"])
    assert exc.value.code == 0
    assert "SlideLite" in capsys.readouterr().out
