"""Tests for previously-disconnected modules: secrets masking, network mocking, snapshots."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Secrets masking
# ---------------------------------------------------------------------------


class TestSecretsMasking:
    def setup_method(self):
        import blop.engine.secrets as s

        s._secrets_cache = None

    def test_mask_text_with_secrets(self, tmp_path):
        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("DB_PASSWORD=SuperSecret123\nAPI_KEY=abc-xyz-999\n")
        os.environ["BLOP_SECRETS_FILE"] = str(secrets_file)

        import blop.engine.secrets as s

        s._secrets_cache = None

        result = s.mask_text("The password is SuperSecret123 and key is abc-xyz-999")
        assert "SuperSecret123" not in result
        assert "abc-xyz-999" not in result
        assert "[REDACTED]" in result

    def test_mask_text_no_secrets(self, tmp_path):
        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("")
        os.environ["BLOP_SECRETS_FILE"] = str(secrets_file)

        import blop.engine.secrets as s

        s._secrets_cache = None

        result = s.mask_text("nothing to redact here")
        assert result == "nothing to redact here"

    def test_mask_dict(self, tmp_path):
        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("TOKEN=mytoken123\n")
        os.environ["BLOP_SECRETS_FILE"] = str(secrets_file)

        import blop.engine.secrets as s

        s._secrets_cache = None

        data = {"key": "contains mytoken123 value", "nested": {"inner": "also mytoken123"}}
        result = s.mask_dict(data)
        assert "mytoken123" not in result["key"]
        assert "mytoken123" not in result["nested"]["inner"]

    def test_has_secrets(self, tmp_path):
        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("SECRET=value\n")
        os.environ["BLOP_SECRETS_FILE"] = str(secrets_file)

        import blop.engine.secrets as s

        s._secrets_cache = None
        assert s.has_secrets() is True

    def test_reload_secrets(self, tmp_path):
        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("A=first\n")
        os.environ["BLOP_SECRETS_FILE"] = str(secrets_file)

        import blop.engine.secrets as s

        s._secrets_cache = None
        assert s.reload_secrets() == 1

        secrets_file.write_text("A=first\nB=second\n")
        assert s.reload_secrets() == 2

    def teardown_method(self):
        os.environ.pop("BLOP_SECRETS_FILE", None)
        import blop.engine.secrets as s

        s._secrets_cache = None


# ---------------------------------------------------------------------------
# Network mocking
# ---------------------------------------------------------------------------


class TestNetworkMocking:
    def setup_method(self):
        from blop.tools.network import _active_routes

        _active_routes.clear()

    @pytest.mark.asyncio
    async def test_mock_and_list_routes(self):
        from blop.tools.network import get_active_routes, mock_network_route

        result = await mock_network_route("**/api/users", status=200, body='{"users":[]}')
        assert result["status"] == "registered"
        assert result["active_routes"] == 1

        routes = get_active_routes()
        assert len(routes) == 1
        assert routes[0]["pattern"] == "**/api/users"

    @pytest.mark.asyncio
    async def test_clear_routes(self):
        from blop.tools.network import clear_network_routes, get_active_routes, mock_network_route

        await mock_network_route("**/api/a")
        await mock_network_route("**/api/b")
        assert len(get_active_routes()) == 2

        result = await clear_network_routes()
        assert result["removed_count"] == 2
        assert len(get_active_routes()) == 0

    @pytest.mark.asyncio
    async def test_multiple_routes(self):
        from blop.tools.network import get_active_routes, mock_network_route

        await mock_network_route("**/api/a", status=200)
        await mock_network_route("**/api/b", status=404, body="not found")
        routes = get_active_routes()
        assert len(routes) == 2
        assert routes[1]["status"] == 404


# ---------------------------------------------------------------------------
# Snapshots (incremental mode)
# ---------------------------------------------------------------------------


class TestSnapshots:
    def test_full_mode(self):
        from blop.engine.snapshots import format_snapshot_for_llm

        nodes = [{"role": "button", "name": "Submit"}]
        os.environ["BLOP_SNAPSHOT_MODE"] = "full"
        result = format_snapshot_for_llm(nodes)
        assert "Submit" in result

    def test_none_mode(self):
        from blop.engine.snapshots import format_snapshot_for_llm

        nodes = [{"role": "button", "name": "Submit"}]
        os.environ["BLOP_SNAPSHOT_MODE"] = "none"
        result = format_snapshot_for_llm(nodes)
        assert result == "[snapshot disabled]"

    def test_incremental_mode(self):
        from blop.engine.snapshots import SnapshotTracker, format_snapshot_for_llm

        os.environ["BLOP_SNAPSHOT_MODE"] = "incremental"
        tracker = SnapshotTracker()

        nodes_v1 = [{"role": "button", "name": "Submit"}, {"role": "link", "name": "Home"}]
        result1 = format_snapshot_for_llm(nodes_v1, tracker)
        assert "Submit" in result1
        assert "Home" in result1

        nodes_v2 = [{"role": "button", "name": "Submit"}, {"role": "link", "name": "About"}]
        result2 = format_snapshot_for_llm(nodes_v2, tracker)
        assert "About" in result2
        assert "Home" in result2  # removed node shows up in delta

        result3 = format_snapshot_for_llm(nodes_v2, tracker)
        assert result3 == "[no changes since last snapshot]"

    def test_tracker_reset(self):
        from blop.engine.snapshots import SnapshotTracker

        tracker = SnapshotTracker()
        tracker.compute_delta([{"role": "button", "name": "A"}])
        tracker.reset()
        delta = tracker.compute_delta([{"role": "button", "name": "A"}])
        assert len(delta) == 1  # After reset, everything is new again

    def test_incremental_mode_duplicate_nodes_stable_on_reorder(self):
        from blop.engine.snapshots import SnapshotTracker

        tracker = SnapshotTracker()
        nodes_v1 = [
            {"role": "button", "name": "Submit", "metadata": {"variant": "primary"}},
            {"role": "button", "name": "Submit", "metadata": {"variant": "secondary"}},
        ]
        first_delta = tracker.compute_delta(nodes_v1)
        assert len(first_delta) == 2

        # Same logical nodes, opposite insertion order.
        nodes_v2 = [nodes_v1[1], nodes_v1[0]]
        second_delta = tracker.compute_delta(nodes_v2)
        assert second_delta == []

    def teardown_method(self):
        os.environ.pop("BLOP_SNAPSHOT_MODE", None)


# ---------------------------------------------------------------------------
# Visual regression (pixel_diff — offline, no browser needed)
# ---------------------------------------------------------------------------


class TestVisualRegressionPixelDiff:
    def test_pixel_diff_identical(self, tmp_path):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        path_a = str(tmp_path / "a.png")
        path_b = str(tmp_path / "b.png")
        img.save(path_a)
        img.save(path_b)

        from blop.engine.visual_regression import pixel_diff

        ratio, diff_path, size_mismatch = pixel_diff(path_a, path_b)
        assert ratio == 0.0
        assert size_mismatch is False

    def test_pixel_diff_different(self, tmp_path):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        img_a = Image.new("RGB", (100, 100), color=(255, 0, 0))
        img_b = Image.new("RGB", (100, 100), color=(0, 0, 255))
        path_a = str(tmp_path / "a.png")
        path_b = str(tmp_path / "b.png")
        img_a.save(path_a)
        img_b.save(path_b)

        from blop.engine.visual_regression import pixel_diff

        ratio, diff_path, size_mismatch = pixel_diff(path_a, path_b)
        assert ratio > 0.5
        assert diff_path is not None
        assert Path(diff_path).exists()
        assert size_mismatch is False

    def test_pixel_diff_size_mismatch(self, tmp_path):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        img_a = Image.new("RGB", (100, 100), color=(255, 0, 0))
        img_b = Image.new("RGB", (120, 100), color=(255, 0, 0))
        path_a = str(tmp_path / "a.png")
        path_b = str(tmp_path / "b.png")
        img_a.save(path_a)
        img_b.save(path_b)

        from blop.engine.visual_regression import pixel_diff

        ratio, diff_path, size_mismatch = pixel_diff(path_a, path_b)
        assert ratio == 1.0
        assert diff_path is None
        assert size_mismatch is True
