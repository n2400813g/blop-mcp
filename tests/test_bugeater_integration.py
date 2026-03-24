"""Live Bug Eater integration coverage for exploration and smoke replay.

Marks: happy_path, slow, integration.

Skip conditions:
  - No configured LLM API key after blop config loads the repo .env
  - Network not reachable (BLOP_SKIP_NETWORK_TESTS=1)
  - Chromium cannot launch

Environment knobs:
  TEST_URL             default: https://bugeater.web.app
  BUGEATER_APP_URL     default: https://bugeater.web.app/app/list
  BUGEATER_MAX_PAGES   default: 10
  BUGEATER_SMOKE_TASKS optional JSON array of objects with
                       flow_name, goal, business_criticality
  BUGEATER_REPLAY_TIMEOUT_SECS default: 180
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
from urllib.parse import urlparse

import pytest

from blop.config import check_llm_api_key

BUGEATER_BASE_URL = os.getenv("TEST_URL", "https://bugeater.web.app")
BUGEATER_APP_URL = os.getenv("BUGEATER_APP_URL", f"{BUGEATER_BASE_URL.rstrip('/')}/app/list")
BUGEATER_MAX_PAGES = int(os.getenv("BUGEATER_MAX_PAGES", "10"))
BUGEATER_REPLAY_TIMEOUT_SECS = int(os.getenv("BUGEATER_REPLAY_TIMEOUT_SECS", "180"))

_DEFAULT_SMOKE_TASKS = [
    {
        "flow_name": "bugeater_challenge_navigation",
        "goal": (
            "Navigate to https://bugeater.web.app/app/list, dismiss cookie notices or tutorial overlays if they appear, "
            "verify the page shows 'List of Challenges', open 'Challenge #1.1: Number Addition', "
            "then return to the challenge list and confirm challenge categories are still visible."
        ),
        "business_criticality": "activation",
    },
    {
        "flow_name": "bugeater_number_addition",
        "goal": (
            "Navigate to https://bugeater.web.app/app/challenge/learn/adder, dismiss cookie notices or tutorial overlays if they appear, "
            "enter 1 in the first number field, enter 2 in the second number field, click 'Calculate!', "
            "and verify the visible result is 3."
        ),
        "business_criticality": "activation",
    },
    {
        "flow_name": "bugeater_create_profile",
        "goal": (
            "Navigate to https://bugeater.web.app/app/challenge/scripted/createProfile, dismiss cookie notices or tutorial overlays if they appear, "
            "enter Nickname tech_go1, enter Last Name Anderson, choose Birth Year 2000, click 'Submit!', "
            "and verify a visible result message says 'Your profile created'."
        ),
        "business_criticality": "support",
    },
]

_CHALLENGE_KEYWORDS = (
    "challenge",
    "calculator",
    "addition",
    "profile",
    "email",
    "hotel",
    "division",
    "todo",
    "password",
    "number",
)

_DISCOVERY_EXECUTION_KEYWORDS = _CHALLENGE_KEYWORDS + (
    "form",
    "forms",
    "validation",
    "submit",
    "input",
)


def _host_resolves(url: str) -> bool:
    hostname = urlparse(url).hostname
    if not hostname:
        return False
    try:
        socket.gethostbyname(hostname)
        return True
    except OSError:
        return False


def _chromium_launchable() -> bool:
    async def _probe() -> bool:
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                await browser.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


def _load_smoke_tasks() -> list[dict[str, str]]:
    raw = os.getenv("BUGEATER_SMOKE_TASKS", "").strip()
    if not raw:
        return list(_DEFAULT_SMOKE_TASKS)

    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("BUGEATER_SMOKE_TASKS must be a non-empty JSON list")

    tasks: list[dict[str, str]] = []
    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"BUGEATER_SMOKE_TASKS item #{idx} must be an object")
        flow_name = str(item.get("flow_name", "")).strip()
        goal = str(item.get("goal", "")).strip()
        business_criticality = str(item.get("business_criticality", "other")).strip() or "other"
        if not flow_name or not goal:
            raise ValueError(
                f"BUGEATER_SMOKE_TASKS item #{idx} must include non-empty flow_name and goal"
            )
        tasks.append(
            {
                "flow_name": flow_name,
                "goal": goal,
                "business_criticality": business_criticality,
            }
        )
    return tasks


def _contains_challenge_signal(value: object) -> bool:
    text = json.dumps(value, sort_keys=True).lower() if not isinstance(value, str) else value.lower()
    return any(keyword in text for keyword in _CHALLENGE_KEYWORDS)


async def _poll_run_until_terminal(run_id: str, timeout_secs: int) -> dict:
    from blop.tools.results import get_test_results

    deadline = asyncio.get_running_loop().time() + timeout_secs
    last_result: dict | None = None

    while True:
        last_result = await get_test_results(run_id=run_id)
        if "error" in last_result:
            return last_result
        if last_result.get("status") not in {"queued", "running"}:
            return last_result
        if asyncio.get_running_loop().time() >= deadline:
            return last_result
        await asyncio.sleep(2)


_has_api_key, _key_name = check_llm_api_key()
_skip_network = os.getenv("BLOP_SKIP_NETWORK_TESTS", "0") == "1"
_host_unreachable = not _skip_network and not _host_resolves(BUGEATER_APP_URL)
_chromium_unavailable = not _skip_network and not _host_unreachable and not _chromium_launchable()
_skip_reason = (
    f"{_key_name} not set — skipping live integration tests"
    if not _has_api_key
    else "BLOP_SKIP_NETWORK_TESTS=1"
    if _skip_network
    else f"Host for {BUGEATER_APP_URL} is not reachable from this environment"
    if _host_unreachable
    else "Chromium cannot launch in this environment"
    if _chromium_unavailable
    else None
)
_skip = bool(_skip_reason)


@pytest.fixture(scope="class")
def live_db(tmp_path_factory):
    """Shared DB and runs dir for the full Bug Eater workflow."""
    from blop.storage.sqlite import init_db

    root = tmp_path_factory.mktemp("bugeater-live")
    db_path = root / "bugeater.db"
    runs_dir = root / "runs"

    original_db_path = os.environ.get("BLOP_DB_PATH")
    original_runs_dir = os.environ.get("BLOP_RUNS_DIR")
    original_targeted_max_steps = os.environ.get("BLOP_TARGETED_MAX_STEPS")
    original_max_steps = os.environ.get("BLOP_MAX_STEPS")
    os.environ["BLOP_DB_PATH"] = str(db_path)
    os.environ["BLOP_RUNS_DIR"] = str(runs_dir)
    os.environ["BLOP_TARGETED_MAX_STEPS"] = os.getenv("BLOP_TARGETED_MAX_STEPS", "12")
    os.environ["BLOP_MAX_STEPS"] = os.getenv("BLOP_MAX_STEPS", "25")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()

    yield {"db_path": str(db_path), "runs_dir": str(runs_dir)}

    if original_db_path is None:
        os.environ.pop("BLOP_DB_PATH", None)
    else:
        os.environ["BLOP_DB_PATH"] = original_db_path

    if original_runs_dir is None:
        os.environ.pop("BLOP_RUNS_DIR", None)
    else:
        os.environ["BLOP_RUNS_DIR"] = original_runs_dir

    if original_targeted_max_steps is None:
        os.environ.pop("BLOP_TARGETED_MAX_STEPS", None)
    else:
        os.environ["BLOP_TARGETED_MAX_STEPS"] = original_targeted_max_steps

    if original_max_steps is None:
        os.environ.pop("BLOP_MAX_STEPS", None)
    else:
        os.environ["BLOP_MAX_STEPS"] = original_max_steps


@pytest.mark.skipif(_skip, reason=_skip_reason or "")
@pytest.mark.happy_path
@pytest.mark.slow
@pytest.mark.integration
class TestBugEaterIntegration:
    """Sequential live checks against the Bug Eater challenge library."""

    _targeted_run_id: str = ""
    _targeted_release_id: str = ""
    _replay_run_id: str = ""
    _replay_release_id: str = ""
    _recorded_flow_ids: list[str] = []

    @pytest.mark.asyncio
    async def test_01_validate_release_setup_returns_ready(self, live_db):
        from blop.tools.validate import validate_release_setup

        result = await validate_release_setup(app_url=BUGEATER_APP_URL)
        assert result["status"] in ("ready", "warnings"), (
            f"validate_release_setup returned unexpected status: {result}"
        )
        assert result["blockers"] == [], f"Unexpected blockers: {result['blockers']}"

    @pytest.mark.asyncio
    async def test_02_explore_site_inventory_discovers_challenge_routes(self, live_db):
        from blop.tools.discover import explore_site_inventory

        result = await explore_site_inventory(
            app_url=BUGEATER_APP_URL,
            max_depth=2,
            max_pages=max(8, BUGEATER_MAX_PAGES),
        )

        assert "inventory" in result, f"Missing inventory payload: {result}"
        inventory = result["inventory"]
        assert inventory.get("page_structures"), f"Expected page structures in inventory: {inventory}"

        route_count = len(inventory.get("routes", []))
        structure_count = len(inventory.get("page_structures", {}))
        assert route_count >= 3 or structure_count >= 2, (
            f"Expected multiple discovered routes or structures, got routes={route_count}, structures={structure_count}"
        )
        assert _contains_challenge_signal(inventory), (
            f"Inventory did not include recognizable Bug Eater challenge signals: {inventory}"
        )

    @pytest.mark.asyncio
    async def test_03_discover_critical_journeys_finds_challenge_work(self, live_db):
        from blop.tools.journeys import discover_critical_journeys

        result = await discover_critical_journeys(
            app_url=BUGEATER_APP_URL,
            business_goal=(
                "Find the most important Bug Eater challenge journeys covering challenge discovery, "
                "calculator-style form execution, and profile creation."
            ),
            max_depth=2,
            max_pages=max(8, BUGEATER_MAX_PAGES),
        )

        assert "journeys" in result, f"Missing journeys key: {result}"
        assert result["journey_count"] >= 3, (
            f"Expected at least 3 journeys, got {result['journey_count']}"
        )
        journey_texts = [
            " ".join(
                [
                    str(journey.get("journey_name", "")),
                    str(journey.get("why_it_matters", "")),
                    str(journey.get("gating_reason", "")),
                ]
            ).lower()
            for journey in result["journeys"]
        ]
        challenge_journeys = [
            text for text in journey_texts
            if any(keyword in text for keyword in _DISCOVERY_EXECUTION_KEYWORDS)
        ]
        assert challenge_journeys, (
            "Expected at least one challenge or form-execution-oriented journey, "
            f"got: {result['journeys']}"
        )
        assert any(
            "form" in text or "validation" in text
            for text in challenge_journeys
        ), f"Expected at least one execution-focused journey, got: {result['journeys']}"

    @pytest.mark.asyncio
    async def test_04_run_release_check_targeted_mode_returns_evidence(self, live_db):
        from blop.tools.release_check import run_release_check

        result = await run_release_check(
            app_url=BUGEATER_APP_URL,
            mode="targeted",
            headless=True,
        )

        assert "decision" in result, f"Missing decision: {result}"
        assert result["decision"] in ("SHIP", "INVESTIGATE", "BLOCK"), (
            f"Unexpected decision: {result['decision']}"
        )
        assert result.get("run_id"), f"Targeted run did not return run_id: {result}"
        assert result.get("status") in ("completed", "failed"), (
            f"Expected targeted run to complete synchronously, got: {result.get('status')}"
        )
        assert result.get("evidence_summary") or result.get("cases"), (
            f"Expected usable evidence in targeted result: {result}"
        )

        TestBugEaterIntegration._targeted_release_id = result.get("release_id", "")
        TestBugEaterIntegration._targeted_run_id = result.get("run_id", "")

    @pytest.mark.asyncio
    async def test_05_get_test_results_for_targeted_run(self, live_db):
        from blop.tools.results import get_test_results

        if not self._targeted_run_id:
            pytest.skip("No run_id from targeted release check")

        result = await get_test_results(run_id=self._targeted_run_id)
        assert result.get("status") in ("completed", "failed"), (
            f"Expected completed/failed targeted result, got: {result.get('status')}"
        )
        assert result.get("decision_summary"), f"Expected decision summary in results: {result}"

    @pytest.mark.asyncio
    async def test_06_record_smoke_flows(self, live_db):
        from blop.tools.evaluate import evaluate_web_task

        smoke_tasks = _load_smoke_tasks()
        recorded_flow_ids: list[str] = []

        for task in smoke_tasks:
            result = await evaluate_web_task(
                app_url=BUGEATER_APP_URL,
                task=task["goal"],
                save_as_recorded_flow=True,
                flow_name=task["flow_name"],
                max_steps=12,
                headless=True,
            )
            assert result.get("run_id"), f"Smoke evaluation did not create a run: {result}"
            assert result.get("recorded_flow_id"), (
                f"Smoke evaluation did not produce a recorded flow for {task['flow_name']}: {result}"
            )
            recorded_flow_ids.append(result["recorded_flow_id"])

        assert len(recorded_flow_ids) >= 2, f"Expected at least 2 recorded smoke flows, got: {recorded_flow_ids}"
        TestBugEaterIntegration._recorded_flow_ids = recorded_flow_ids

    @pytest.mark.asyncio
    async def test_07_run_replay_and_poll_for_results(self, live_db):
        from blop.tools.release_check import run_release_check

        if not self._recorded_flow_ids:
            pytest.skip("No recorded flows available for replay")

        start = await run_release_check(
            app_url=BUGEATER_APP_URL,
            flow_ids=self._recorded_flow_ids,
            mode="replay",
            headless=True,
        )

        assert start.get("run_id"), f"Replay start did not return run_id: {start}"
        assert start.get("status") in {"queued", "running", "completed", "failed"}, (
            f"Unexpected replay start status: {start}"
        )

        final_result = await _poll_run_until_terminal(
            run_id=start["run_id"],
            timeout_secs=BUGEATER_REPLAY_TIMEOUT_SECS,
        )

        assert "error" not in final_result, f"Replay polling failed: {final_result}"
        assert final_result.get("status") in ("completed", "failed"), (
            f"Expected replay run to reach terminal status, got: {final_result.get('status')}"
        )
        assert final_result.get("cases"), f"Expected case-level replay results: {final_result}"

        TestBugEaterIntegration._replay_release_id = start.get("release_id", "")
        TestBugEaterIntegration._replay_run_id = start["run_id"]

    @pytest.mark.asyncio
    async def test_08_debug_failed_case_if_any(self, live_db):
        from blop.tools.debug import debug_test_case
        from blop.tools.results import get_test_results

        if not self._replay_run_id:
            pytest.skip("No replay run available for debug")

        results = await get_test_results(run_id=self._replay_run_id)
        failed_cases = [
            case for case in results.get("cases", [])
            if case.get("status") in ("fail", "error", "blocked")
        ]
        if not failed_cases:
            pytest.skip("Replay smoke flows did not produce a failing case to debug")

        case_id = failed_cases[0]["case_id"]
        debug_result = await debug_test_case(run_id=self._replay_run_id, case_id=case_id)
        assert "error" not in debug_result, f"debug_test_case failed: {debug_result}"
        assert debug_result.get("case_id") == case_id, f"Unexpected debug payload: {debug_result}"
        assert (
            debug_result.get("screenshots")
            or debug_result.get("console_log")
            or debug_result.get("why_failed")
            or debug_result.get("repro_steps")
        ), f"Expected actionable debug evidence: {debug_result}"
