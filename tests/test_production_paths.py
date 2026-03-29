from __future__ import annotations

from pathlib import Path


def test_runs_dir_resolves_relative_to_repo_root(monkeypatch, tmp_path):
    from blop import config
    from blop.storage import files

    monkeypatch.setattr(files, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(config, "BLOP_RUNS_DIR", "runs-prod")

    assert files._runs_dir() == tmp_path / "runs-prod"


def test_runs_dir_preserves_absolute_path(monkeypatch, tmp_path):
    from blop import config
    from blop.storage import files

    absolute_runs = tmp_path / "absolute-runs"
    monkeypatch.setattr(config, "BLOP_RUNS_DIR", str(absolute_runs))

    assert files._runs_dir() == absolute_runs


def test_screenshot_path_stays_inside_configured_runs_dir(monkeypatch, tmp_path):
    from blop import config
    from blop.storage import files

    absolute_runs = tmp_path / "runs"
    monkeypatch.setattr(config, "BLOP_RUNS_DIR", str(absolute_runs))

    path = Path(files.screenshot_path("run-1", "case-1", 0))

    assert str(path).startswith(str(absolute_runs))
    assert path.name == "step_000.png"
