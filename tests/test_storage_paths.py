from __future__ import annotations

import pytest

from blop.storage import files


def test_screenshot_path_rejects_path_traversal():
    with pytest.raises(ValueError):
        files.screenshot_path("../bad", "case1", 1)


def test_runs_dir_anchors_relative_override(monkeypatch):
    monkeypatch.setenv("BLOP_RUNS_DIR", "runs-custom")
    path = files._runs_dir()
    assert path.is_absolute()
