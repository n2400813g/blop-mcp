from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from blop.schemas import FailureCase


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,ext", [("markdown", ".md"), ("html", ".html"), ("json", ".json")])
async def test_export_test_report_writes_expected_format(tmp_path, fmt: str, ext: str):
    from blop.reporting.export import export_test_report
    from blop.storage import sqlite

    db_path = str(tmp_path / "export.db")
    run_id = f"run_export_{fmt}"
    artifacts_path = tmp_path / "artifacts"
    artifacts_path.mkdir(parents=True, exist_ok=True)

    with patch.dict(os.environ, {"BLOP_DB_PATH": db_path}, clear=False):
        await sqlite.init_db()
        await sqlite.create_run(
            run_id=run_id,
            app_url="https://example.com",
            profile_name=None,
            flow_ids=["flow1"],
            headless=True,
            artifacts_dir=str(artifacts_path),
            run_mode="hybrid",
        )
        await sqlite.update_run_status(run_id, "completed")
        await sqlite.save_case(
            FailureCase(
                run_id=run_id,
                flow_id="flow1",
                flow_name="checkout",
                status="fail",
                severity="high",
                business_criticality="revenue",
                assertion_failures=["Order confirmation not visible"],
            )
        )

        result = await export_test_report(run_id=run_id, format=fmt)

    assert result["format"] == fmt
    assert result["case_count"] == 1
    output_path = Path(result["path"])
    assert output_path.exists()
    assert output_path.suffix == ext

    content = output_path.read_text()
    if fmt == "markdown":
        assert "Test Run Report" in content
        assert "checkout" in content
    elif fmt == "html":
        assert "<!DOCTYPE html>" in content
        assert "Test Report" in content
    else:
        parsed = json.loads(content)
        assert parsed["run_id"] == run_id
        assert len(parsed["cases"]) == 1
