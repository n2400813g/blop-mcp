from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_includes_build_system_and_release_metadata():
    data = tomllib.loads(Path("pyproject.toml").read_text())

    build_system = data["build-system"]
    project = data["project"]

    assert project["name"] == "blop-mcp"
    assert build_system["build-backend"] == "setuptools.build_meta"
    assert "setuptools>=69" in build_system["requires"]
    assert "wheel" in build_system["requires"]
    assert project["license"] == "MIT"
    assert "Homepage" in project["urls"]
    assert "Documentation" in project["urls"]
    assert "Issue Tracker" in project["urls"]
    assert "build>=1.2.2" in project["optional-dependencies"]["dev"]
