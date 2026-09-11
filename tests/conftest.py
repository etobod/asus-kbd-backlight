"""Shared test fixtures."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def fake_exe(monkeypatch, tmp_path):
    """``fake_exe(name, *siblings, frozen=False)``: create ``tmp_path/name``
    (and any sibling files), point ``sys.executable`` at it and set
    ``sys.frozen``. Returns ``tmp_path``. Undone after the test."""

    def _make(name, *siblings, frozen=False):
        for filename in (name, *siblings):
            (tmp_path / filename).write_bytes(b"")
        monkeypatch.setattr(sys, "executable", str(tmp_path / name))
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        return tmp_path

    return _make
