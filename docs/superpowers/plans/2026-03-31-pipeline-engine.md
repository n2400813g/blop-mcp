# Pipeline Engine + Schema Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 5-stage `RunPipeline` with typed `HealthEvent` emission, `StageError` diagnostics, and `Literal`/`Field` schema hardening — without changing the MCP tool surface.

**Architecture:** `engine/events.py` defines the event taxonomy and `EventBus`. `engine/pipeline.py` holds `RunContext` and `RunPipeline`. Five `engine/stages/*.py` modules wrap existing engine calls and emit typed events. `BlopError` gains `likely_cause`/`suggested_fix`/`retry_safe`. `mcp/envelope.py`'s `ToolError` mirrors these fields. Key tool parameters get `Literal` + `Field` descriptions.

**Tech Stack:** Python 3.12, FastMCP, Pydantic v2, Playwright async, aiosqlite, pytest-asyncio

**Deferred (Phase 4 of spec):** BrowserContextFactory consolidation, config split, server.py split, failure taxonomy unification — out of scope for this plan.

---

## File Map

| Path | Action | Responsibility |
|------|--------|----------------|
| `src/blop/engine/errors.py` | Modify | Add `StageError`, add `likely_cause/suggested_fix/retry_safe` to `BlopError` |
| `src/blop/engine/events.py` | Create | `HealthEvent` Pydantic model, `EventType` Literal, `EventBus` accumulator |
| `src/blop/engine/pipeline.py` | Create | `RunContext` dataclass, `RunPipeline` orchestrator, `build_default_pipeline()`, `persist_bus_events()` |
| `src/blop/engine/stages/__init__.py` | Create | Empty package marker |
| `src/blop/engine/stages/validate.py` | Create | `ValidateStage` — URL validation + VALIDATE_* events |
| `src/blop/engine/stages/auth.py` | Create | `AuthStage` — profile resolution + AUTH_* events |
| `src/blop/engine/stages/execute.py` | Create | `ExecuteStage` — wraps `run_flows()` + STEP_* events |
| `src/blop/engine/stages/classify.py` | Create | `ClassifyStage` — wraps `classify_run()` + CLASSIFY_* events |
| `src/blop/engine/stages/report.py` | Create | `ReportStage` — wraps `build_report()` + REPORT_READY event |
| `src/blop/engine/llm_events.py` | Create | `contextvars`-based LLM_CALL_* event emission |
| `src/blop/mcp/envelope.py` | Modify | Add `likely_cause/suggested_fix/retry_safe/stage` to `ToolError` + `err_response` |
| `src/blop/tools/regression.py` | Modify | Harden `mode`, `flow_ids`, `profile_name` with `Literal`/`Field` |
| `src/blop/tools/evaluate.py` | Modify | Harden `app_url`, `task` with `Field` descriptions + examples |
| `src/blop/tools/journeys.py` | Modify | Harden `app_url`, `business_criticality` with `Literal`/`Field` |
| `src/blop/tools/record.py` | Modify | Harden `business_criticality`, `goal` with `Literal`/`Field` |
| `src/blop/tools/triage.py` | Modify | Harden `severity_filter` with `Literal` |
| `scripts/test_pipeline_live.py` | Create | Live smoke test using `.env` credentials |
| `tests/test_stage_error.py` | Create | StageError + BlopError diagnostic fields |
| `tests/test_health_events.py` | Create | EventBus emit, seq, snapshot |
| `tests/test_pipeline.py` | Create | RunContext defaults, stage ordering, abort on StageError |
| `tests/test_stage_validate.py` | Create | ValidateStage OK + bad URL + trailing slash |
| `tests/test_stage_auth.py` | Create | AuthStage no-profile + resolve + fail |
| `tests/test_stage_execute.py` | Create | ExecuteStage events + step fail details + engine exception |
| `tests/test_stage_classify_report.py` | Create | ClassifyStage + ReportStage events |
| `tests/test_pipeline_integration.py` | Create | Full pipeline happy path + auth abort |
| `tests/test_event_persistence.py` | Create | persist_bus_events storage calls |
| `tests/test_envelope_schema.py` | Create | ToolError fields, err_response, finalize propagation |
| `tests/test_schema_hardening.py` | Create | Tool parameter constraints |
| `tests/test_llm_events.py` | Create | LLM_CALL_* via contextvars |

---

## Task 1: Extend BlopError + add StageError

**Files:**
- Modify: `src/blop/engine/errors.py`
- Create: `tests/test_stage_error.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage_error.py
from blop.engine.errors import BlopError, StageError


def test_stage_error_fields():
    e = StageError(
        stage="AUTH",
        code="BLOP_AUTH_PROFILE_NOT_FOUND",
        message="Profile 'staging' not found.",
        likely_cause="Profile was never created.",
        suggested_fix="Run save_auth_profile first.",
        retry_safe=False,
    )
    assert e.stage == "AUTH"
    assert e.likely_cause == "Profile was never created."
    assert e.suggested_fix == "Run save_auth_profile first."
    assert e.retry_safe is False
    assert e.code == "BLOP_AUTH_PROFILE_NOT_FOUND"
    assert isinstance(e, BlopError)


def test_blop_error_diagnostic_fields_default_empty():
    e = BlopError("BLOP_VALIDATION_FAILED", "bad input")
    assert e.likely_cause == ""
    assert e.suggested_fix == ""
    assert e.retry_safe is False
```

- [ ] **Step 2: Run to verify FAIL**

```bash
cd /Users/alejandrolaurlund/Development/blop-mcp
source .venv/bin/activate
pytest tests/test_stage_error.py -v
```
Expected: FAIL — `ImportError: cannot import name 'StageError'`

- [ ] **Step 3: Add new error codes + extend BlopError + add StageError to `src/blop/engine/errors.py`**

Add these constants before the `BlopError` class:
```python
BLOP_STAGE_VALIDATE_FAILED = "BLOP_STAGE_VALIDATE_FAILED"
BLOP_STAGE_AUTH_FAILED = "BLOP_STAGE_AUTH_FAILED"
BLOP_STAGE_EXECUTE_FAILED = "BLOP_STAGE_EXECUTE_FAILED"
BLOP_STAGE_CLASSIFY_FAILED = "BLOP_STAGE_CLASSIFY_FAILED"
BLOP_STAGE_REPORT_FAILED = "BLOP_STAGE_REPORT_FAILED"
```

Replace the `BlopError.__init__` method (preserve backward compat — all new params have defaults):
```python
def __init__(
    self,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    likely_cause: str = "",
    suggested_fix: str = "",
    retry_safe: bool = False,
) -> None:
    self.code = code
    self.message = message
    self.details = details or {}
    self.retryable = retryable
    self.likely_cause = likely_cause
    self.suggested_fix = suggested_fix
    self.retry_safe = retry_safe
    super().__init__(message)
```

Add `StageError` after the `BlopError` class (add `from typing import Literal` to file imports):
```python
_StageName = Literal["VALIDATE", "AUTH", "EXECUTE", "CLASSIFY", "REPORT"]


class StageError(BlopError):
    """Pipeline stage failure with mandatory diagnostic context."""

    def __init__(
        self,
        stage: "_StageName",
        code: str,
        message: str,
        *,
        likely_cause: str,
        suggested_fix: str,
        retry_safe: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            details=details,
            retryable=retry_safe,
            likely_cause=likely_cause,
            suggested_fix=suggested_fix,
            retry_safe=retry_safe,
        )
        self.stage = stage
```

Add `Literal` to the imports at the top of `errors.py`:
```python
from typing import TYPE_CHECKING, Any, Literal
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/test_stage_error.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/blop/engine/errors.py tests/test_stage_error.py
git commit -m "feat(errors): add StageError + diagnostic fields to BlopError"
```

---

## Task 2: Create HealthEvent model + EventBus

**Files:**
- Create: `src/blop/engine/events.py`
- Create: `tests/test_health_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health_events.py
from datetime import datetime

from blop.engine.events import EventBus, HealthEvent


def test_event_bus_emits_events():
    bus = EventBus("run_001")
    ev = bus.emit("VALIDATE", "VALIDATE_START", "Starting validation")
    assert isinstance(ev, HealthEvent)
    assert ev.run_id == "run_001"
    assert ev.stage == "VALIDATE"
    assert ev.event_type == "VALIDATE_START"
    assert ev.seq == 1
    assert ev.message == "Starting validation"
    assert ev.details == {}
    assert isinstance(ev.timestamp, datetime)


def test_event_bus_seq_increments():
    bus = EventBus("run_002")
    bus.emit("VALIDATE", "VALIDATE_START", "a")
    ev2 = bus.emit("AUTH", "AUTH_START", "b")
    assert ev2.seq == 2


def test_event_bus_details_stored():
    bus = EventBus("run_003")
    ev = bus.emit("EXECUTE", "STEP_FAIL", "Step failed", {"step_index": 3, "selector": "button"})
    assert ev.details["step_index"] == 3
    assert ev.details["selector"] == "button"


def test_event_bus_events_returns_snapshot():
    bus = EventBus("run_004")
    bus.emit("VALIDATE", "VALIDATE_START", "a")
    bus.emit("VALIDATE", "VALIDATE_OK", "b")
    snapshot = bus.events
    assert len(snapshot) == 2
    # Mutating the snapshot does not affect internal list
    snapshot.clear()
    assert len(bus.events) == 2
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_health_events.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'blop.engine.events'`

- [ ] **Step 3: Create `src/blop/engine/events.py`**

```python
"""Typed health events emitted by each pipeline stage during a run."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "VALIDATE_START", "VALIDATE_OK", "VALIDATE_FAIL",
    "AUTH_START", "AUTH_OK", "AUTH_WAITING", "AUTH_FAIL",
    "EXECUTE_START", "STEP_START", "STEP_OK", "STEP_FAIL",
    "STEP_HEALED", "STEP_SKIP", "EXECUTE_DONE",
    "LLM_CALL_START", "LLM_CALL_OK", "LLM_CALL_FAIL", "LLM_CALL_FALLBACK",
    "CLASSIFY_START", "CLASSIFY_OK", "CLASSIFY_FAIL",
    "REPORT_READY",
    "PIPELINE_ABORT",
]

StageName = Literal["VALIDATE", "AUTH", "EXECUTE", "CLASSIFY", "REPORT", "PIPELINE"]


class HealthEvent(BaseModel):
    run_id: str
    stage: StageName
    event_type: EventType
    seq: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class EventBus:
    """In-process event accumulator for one run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._seq = 0
        self._events: list[HealthEvent] = []

    def emit(
        self,
        stage: StageName,
        event_type: EventType,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> HealthEvent:
        self._seq += 1
        ev = HealthEvent(
            run_id=self.run_id,
            stage=stage,
            event_type=event_type,
            seq=self._seq,
            message=message,
            details=details or {},
        )
        self._events.append(ev)
        return ev

    @property
    def events(self) -> list[HealthEvent]:
        return list(self._events)
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/test_health_events.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/blop/engine/events.py tests/test_health_events.py
git commit -m "feat(events): add HealthEvent + EventBus for typed run transparency"
```

---

## Task 3: RunContext + RunPipeline skeleton

**Files:**
- Create: `src/blop/engine/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import pytest

from blop.engine.errors import StageError
from blop.engine.events import EventBus
from blop.engine.pipeline import RunContext, RunPipeline


def test_run_context_defaults():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    assert ctx.validated_url is None
    assert ctx.auth_state is None
    assert ctx.step_results == []
    assert ctx.classified_cases == []
    assert ctx.report is None
    assert isinstance(ctx.bus, EventBus)
    assert ctx.bus.run_id == "r1"


@pytest.mark.asyncio
async def test_pipeline_runs_stages_in_order():
    calls: list[str] = []

    class FakeStage:
        def __init__(self, name: str) -> None:
            self.name = name

        async def run(self, ctx: RunContext) -> None:
            calls.append(self.name)

    pipeline = RunPipeline(
        validate=FakeStage("validate"),
        auth=FakeStage("auth"),
        execute=FakeStage("execute"),
        classify=FakeStage("classify"),
        report=FakeStage("report"),
    )
    ctx = RunContext(run_id="r2", app_url="https://example.com", flow_ids=[], profile_name=None)
    await pipeline.run(ctx)
    assert calls == ["validate", "auth", "execute", "classify", "report"]


@pytest.mark.asyncio
async def test_pipeline_aborts_on_stage_error_and_emits_abort():
    calls: list[str] = []

    class PassStage:
        async def run(self, ctx: RunContext) -> None:
            calls.append("pass")

    class FailStage:
        async def run(self, ctx: RunContext) -> None:
            calls.append("fail")
            raise StageError(
                stage="AUTH",
                code="BLOP_AUTH_PROFILE_NOT_FOUND",
                message="not found",
                likely_cause="x",
                suggested_fix="y",
            )

    class NeverStage:
        async def run(self, ctx: RunContext) -> None:
            calls.append("never")

    pipeline = RunPipeline(
        validate=PassStage(),
        auth=FailStage(),
        execute=NeverStage(),
        classify=NeverStage(),
        report=NeverStage(),
    )
    ctx = RunContext(run_id="r3", app_url="https://example.com", flow_ids=[], profile_name=None)
    with pytest.raises(StageError) as exc_info:
        await pipeline.run(ctx)

    assert calls == ["pass", "fail"]
    assert exc_info.value.stage == "AUTH"
    abort_events = [e for e in ctx.bus.events if e.event_type == "PIPELINE_ABORT"]
    assert len(abort_events) == 1
    assert "AUTH" in abort_events[0].message
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_pipeline.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'blop.engine.pipeline'`

- [ ] **Step 3: Create `src/blop/engine/pipeline.py`**

```python
"""RunContext dataclass and RunPipeline orchestrator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from blop.engine.errors import StageError
from blop.engine.events import EventBus


@dataclass
class RunContext:
    """Mutable context threaded through all pipeline stages for one run."""

    run_id: str
    app_url: str
    flow_ids: list[str]
    profile_name: str | None
    # Populated by VALIDATE:
    validated_url: str | None = None
    browser_config: dict[str, Any] = field(default_factory=dict)
    # Populated by AUTH:
    auth_state: str | None = None
    # Populated by EXECUTE:
    step_results: list[Any] = field(default_factory=list)
    # Populated by CLASSIFY:
    classified_cases: list[Any] = field(default_factory=list)
    # Populated by REPORT:
    report: dict[str, Any] | None = None
    # Event bus — created in __post_init__
    bus: EventBus = field(init=False)

    def __post_init__(self) -> None:
        self.bus = EventBus(self.run_id)


@runtime_checkable
class Stage(Protocol):
    async def run(self, ctx: RunContext) -> None: ...


class RunPipeline:
    """Orchestrates VALIDATE → AUTH → EXECUTE → CLASSIFY → REPORT in order."""

    def __init__(
        self,
        *,
        validate: Stage,
        auth: Stage,
        execute: Stage,
        classify: Stage,
        report: Stage,
    ) -> None:
        self.validate = validate
        self.auth = auth
        self.execute = execute
        self.classify = classify
        self.report = report

    async def run(self, ctx: RunContext) -> None:
        stage_order = [
            ("VALIDATE", self.validate),
            ("AUTH", self.auth),
            ("EXECUTE", self.execute),
            ("CLASSIFY", self.classify),
            ("REPORT", self.report),
        ]
        for stage_name, stage in stage_order:
            try:
                await stage.run(ctx)
            except StageError as exc:
                ctx.bus.emit(
                    "PIPELINE",
                    "PIPELINE_ABORT",
                    f"Pipeline aborted at {stage_name}: {exc.message}",
                    details={
                        "stage": exc.stage,
                        "code": exc.code,
                        "likely_cause": exc.likely_cause,
                        "suggested_fix": exc.suggested_fix,
                        "retry_safe": exc.retry_safe,
                    },
                )
                raise


def build_default_pipeline() -> RunPipeline:
    """Build the standard 5-stage pipeline with production stage implementations."""
    from blop.engine.stages.auth import AuthStage
    from blop.engine.stages.classify import ClassifyStage
    from blop.engine.stages.execute import ExecuteStage
    from blop.engine.stages.report import ReportStage
    from blop.engine.stages.validate import ValidateStage

    return RunPipeline(
        validate=ValidateStage(),
        auth=AuthStage(),
        execute=ExecuteStage(),
        classify=ClassifyStage(),
        report=ReportStage(),
    )


async def persist_bus_events(bus: EventBus) -> None:
    """Persist all events in the bus to the run_health_events table."""
    import asyncio

    async def _save(ev: "HealthEvent") -> None:
        try:
            from blop.storage.sqlite import save_run_health_event
            await save_run_health_event(
                run_id=ev.run_id,
                event_type=ev.event_type,
                stage=ev.stage,
                message=ev.message,
                details=ev.details,
            )
        except Exception:
            pass  # best-effort; do not fail the run on persistence errors

    from blop.engine.events import HealthEvent  # noqa: F401 (type ref only)
    tasks = [_save(ev) for ev in bus.events]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/test_pipeline.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/blop/engine/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): add RunContext + RunPipeline + build_default_pipeline"
```

---

## Task 4: Stage 1 — VALIDATE

**Files:**
- Create: `src/blop/engine/stages/__init__.py`
- Create: `src/blop/engine/stages/validate.py`
- Create: `tests/test_stage_validate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage_validate.py
import pytest
from unittest.mock import patch

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext
from blop.engine.stages.validate import ValidateStage


@pytest.mark.asyncio
async def test_validate_stage_ok():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=[], profile_name=None)
    with patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com"):
        await ValidateStage().run(ctx)
    assert ctx.validated_url == "https://example.com"
    event_types = [e.event_type for e in ctx.bus.events]
    assert "VALIDATE_START" in event_types
    assert "VALIDATE_OK" in event_types


@pytest.mark.asyncio
async def test_validate_stage_bad_url_raises_stage_error():
    ctx = RunContext(run_id="r2", app_url="not-a-url", flow_ids=[], profile_name=None)
    with patch("blop.engine.stages.validate.validate_app_url", side_effect=ValueError("invalid scheme")):
        with pytest.raises(StageError) as exc_info:
            await ValidateStage().run(ctx)
    assert exc_info.value.stage == "VALIDATE"
    assert exc_info.value.retry_safe is False
    assert exc_info.value.suggested_fix != ""
    assert any(e.event_type == "VALIDATE_FAIL" for e in ctx.bus.events)


@pytest.mark.asyncio
async def test_validate_stage_strips_trailing_slash():
    ctx = RunContext(run_id="r3", app_url="https://example.com/", flow_ids=[], profile_name=None)
    with patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com/"):
        await ValidateStage().run(ctx)
    assert not (ctx.validated_url or "").endswith("/")
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_stage_validate.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'blop.engine.stages'`

- [ ] **Step 3: Create `src/blop/engine/stages/__init__.py`** (empty file)

- [ ] **Step 4: Create `src/blop/engine/stages/validate.py`**

```python
"""Stage 1: VALIDATE — verify app_url is well-formed and configuration is sane."""
from __future__ import annotations

from blop.engine.errors import BLOP_URL_VALIDATION_FAILED, StageError
from blop.engine.pipeline import RunContext


def validate_app_url(url: str) -> str:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.config import validate_app_url as _v
    return _v(url)


class ValidateStage:
    async def run(self, ctx: RunContext) -> None:
        ctx.bus.emit("VALIDATE", "VALIDATE_START", f"Validating app_url: {ctx.app_url}")
        try:
            url = validate_app_url(ctx.app_url)
        except Exception as exc:
            ctx.bus.emit("VALIDATE", "VALIDATE_FAIL", f"URL validation failed: {exc}")
            raise StageError(
                stage="VALIDATE",
                code=BLOP_URL_VALIDATION_FAILED,
                message=str(exc),
                likely_cause=(
                    "The app_url is malformed, uses an unsupported scheme, "
                    "or is blocked by BLOP_ALLOWED_URL_PATTERN."
                ),
                suggested_fix=(
                    "Check that app_url starts with http:// or https://, "
                    "is reachable from this machine, and is not in BLOP_BLOCKED_URL_PATTERNS."
                ),
                retry_safe=False,
            ) from exc

        ctx.validated_url = url.rstrip("/")
        ctx.bus.emit("VALIDATE", "VALIDATE_OK", f"URL validated: {ctx.validated_url}")
```

- [ ] **Step 5: Run to verify PASS**

```bash
pytest tests/test_stage_validate.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/blop/engine/stages/ tests/test_stage_validate.py
git commit -m "feat(stages): add ValidateStage (Stage 1)"
```

---

## Task 5: Stage 2 — AUTH

**Files:**
- Create: `src/blop/engine/stages/auth.py`
- Create: `tests/test_stage_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage_auth.py
import pytest
from unittest.mock import AsyncMock, patch

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext
from blop.engine.stages.auth import AuthStage


@pytest.mark.asyncio
async def test_auth_stage_no_profile():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=[], profile_name=None)
    ctx.validated_url = "https://example.com"
    await AuthStage().run(ctx)
    assert ctx.auth_state is None
    event_types = [e.event_type for e in ctx.bus.events]
    assert "AUTH_START" in event_types
    assert "AUTH_OK" in event_types


@pytest.mark.asyncio
async def test_auth_stage_resolves_profile():
    ctx = RunContext(run_id="r2", app_url="https://example.com", flow_ids=[], profile_name="staging")
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.auth.resolve_storage_state", new=AsyncMock(return_value="/tmp/state.json")):
        await AuthStage().run(ctx)
    assert ctx.auth_state == "/tmp/state.json"
    assert any(e.event_type == "AUTH_OK" for e in ctx.bus.events)


@pytest.mark.asyncio
async def test_auth_stage_fail_raises_stage_error_with_profile_name():
    ctx = RunContext(run_id="r3", app_url="https://example.com", flow_ids=[], profile_name="missing_profile")
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.auth.resolve_storage_state", new=AsyncMock(side_effect=Exception("not found"))):
        with pytest.raises(StageError) as exc_info:
            await AuthStage().run(ctx)
    assert exc_info.value.stage == "AUTH"
    assert "missing_profile" in exc_info.value.suggested_fix
    assert any(e.event_type == "AUTH_FAIL" for e in ctx.bus.events)
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_stage_auth.py -v
```
Expected: FAIL

- [ ] **Step 3: Create `src/blop/engine/stages/auth.py`**

```python
"""Stage 2: AUTH — resolve and validate the auth profile, acquire storage_state."""
from __future__ import annotations

from blop.engine.errors import BLOP_AUTH_PROFILE_NOT_FOUND, StageError
from blop.engine.pipeline import RunContext


async def resolve_storage_state(profile_name: str, app_url: str) -> str | None:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.engine.auth import resolve_storage_state as _r
    return await _r(profile_name, app_url)


class AuthStage:
    async def run(self, ctx: RunContext) -> None:
        profile = ctx.profile_name
        ctx.bus.emit("AUTH", "AUTH_START", f"Resolving auth profile: {profile or '(none)'}")

        if profile is None:
            ctx.auth_state = None
            ctx.bus.emit("AUTH", "AUTH_OK", "No auth profile required — proceeding unauthenticated")
            return

        try:
            state = await resolve_storage_state(profile, ctx.validated_url or ctx.app_url)
            ctx.auth_state = state
            ctx.bus.emit("AUTH", "AUTH_OK", f"Auth profile '{profile}' resolved successfully")
        except Exception as exc:
            ctx.bus.emit("AUTH", "AUTH_FAIL", f"Auth resolution failed for '{profile}': {exc}")
            raise StageError(
                stage="AUTH",
                code=BLOP_AUTH_PROFILE_NOT_FOUND,
                message=f"Auth profile '{profile}' could not be resolved: {exc}",
                likely_cause=(
                    f"Profile '{profile}' was not created, its storage_state file is missing, "
                    "or the session has expired."
                ),
                suggested_fix=(
                    f"Run save_auth_profile with profile_name='{profile}' to create the profile, "
                    f"or run capture_auth_session to re-capture a fresh session for '{profile}'."
                ),
                retry_safe=False,
                details={"profile_name": profile, "error": str(exc)},
            ) from exc
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/test_stage_auth.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/blop/engine/stages/auth.py tests/test_stage_auth.py
git commit -m "feat(stages): add AuthStage (Stage 2)"
```

---

## Task 6: Stage 3 — EXECUTE

**Files:**
- Create: `src/blop/engine/stages/execute.py`
- Create: `tests/test_stage_execute.py`

The EXECUTE stage wraps the existing `regression_engine.run_flows()` and translates its output into typed health events. The replay logic stays in `regression.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage_execute.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext
from blop.engine.stages.execute import ExecuteStage


def _make_case(flow_id="f1", status="passed", step_results=None):
    case = MagicMock()
    case.flow_id = flow_id
    case.status = status
    case.step_results = step_results or []
    case.case_id = "case_001"
    return case


@pytest.mark.asyncio
async def test_execute_stage_emits_start_and_done():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.execute.run_flows", new=AsyncMock(return_value=[_make_case()])):
        await ExecuteStage().run(ctx)
    event_types = [e.event_type for e in ctx.bus.events]
    assert "EXECUTE_START" in event_types
    assert "EXECUTE_DONE" in event_types
    assert len(ctx.step_results) == 1


@pytest.mark.asyncio
async def test_execute_stage_emits_step_fail_with_details():
    ctx = RunContext(run_id="r2", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    ctx.validated_url = "https://example.com"

    step = MagicMock()
    step.status = "failed"
    step.selector = "button.checkout"
    step.action = "click"
    step.screenshot_path = "runs/screenshots/r2/step_01.png"
    step.console_errors = ["TypeError: null"]
    step.healed = False
    step.healing_attempted = True

    fake_case = _make_case(status="failed", step_results=[step])
    with patch("blop.engine.stages.execute.run_flows", new=AsyncMock(return_value=[fake_case])):
        await ExecuteStage().run(ctx)

    fail_events = [e for e in ctx.bus.events if e.event_type == "STEP_FAIL"]
    assert len(fail_events) == 1
    assert fail_events[0].details["selector"] == "button.checkout"
    assert fail_events[0].details["action"] == "click"
    assert fail_events[0].details["healing_attempted"] is True


@pytest.mark.asyncio
async def test_execute_stage_wraps_engine_exception_as_stage_error():
    ctx = RunContext(run_id="r3", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    ctx.validated_url = "https://example.com"
    with patch("blop.engine.stages.execute.run_flows", new=AsyncMock(side_effect=RuntimeError("browser crash"))):
        with pytest.raises(StageError) as exc_info:
            await ExecuteStage().run(ctx)
    assert exc_info.value.stage == "EXECUTE"
    assert exc_info.value.retry_safe is True
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_stage_execute.py -v
```
Expected: FAIL

- [ ] **Step 3: Create `src/blop/engine/stages/execute.py`**

```python
"""Stage 3: EXECUTE — replay recorded flows and emit per-step health events."""
from __future__ import annotations

from typing import Any

from blop.engine.errors import BLOP_STAGE_EXECUTE_FAILED, StageError
from blop.engine.pipeline import RunContext


async def run_flows(
    flow_ids: list[str],
    app_url: str,
    storage_state: str | None,
) -> list[Any]:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.engine.regression import run_flows as _run_flows
    return await _run_flows(flow_ids, app_url=app_url, storage_state=storage_state)


class ExecuteStage:
    async def run(self, ctx: RunContext) -> None:
        ctx.bus.emit(
            "EXECUTE", "EXECUTE_START",
            f"Starting replay of {len(ctx.flow_ids)} flow(s)",
            {"flow_count": len(ctx.flow_ids), "flow_ids": ctx.flow_ids},
        )

        try:
            cases = await run_flows(
                ctx.flow_ids,
                app_url=ctx.validated_url or ctx.app_url,
                storage_state=ctx.auth_state,
            )
        except Exception as exc:
            ctx.bus.emit("EXECUTE", "EXECUTE_DONE", f"Execution failed: {exc}")
            raise StageError(
                stage="EXECUTE",
                code=BLOP_STAGE_EXECUTE_FAILED,
                message=f"Flow execution failed: {exc}",
                likely_cause="Browser crashed, network error, or Playwright timeout during replay.",
                suggested_fix=(
                    "Check BLOP_STEP_TIMEOUT_SECS (default 30s), verify the app is reachable, "
                    "and check for browser console errors in the run artifacts."
                ),
                retry_safe=True,
                details={"error": str(exc)},
            ) from exc

        for case in cases:
            self._emit_case_events(ctx, case)
            ctx.step_results.append(case)

        passed = sum(1 for c in cases if getattr(c, "status", None) == "passed")
        failed = len(cases) - passed
        ctx.bus.emit(
            "EXECUTE", "EXECUTE_DONE",
            f"Execution complete: {passed} passed, {failed} failed",
            {"total": len(cases), "passed": passed, "failed": failed},
        )

    def _emit_case_events(self, ctx: RunContext, case: Any) -> None:
        for i, step in enumerate(getattr(case, "step_results", []) or []):
            status = getattr(step, "status", "unknown")
            healed = getattr(step, "healed", False)
            selector = getattr(step, "selector", "")
            action = getattr(step, "action", "")

            if status == "passed" and not healed:
                ctx.bus.emit(
                    "EXECUTE", "STEP_OK",
                    f"Step {i + 1} passed ({action})",
                    {"step_index": i, "selector": selector, "action": action},
                )
            elif healed:
                ctx.bus.emit(
                    "EXECUTE", "STEP_HEALED",
                    f"Step {i + 1} healed ({action})",
                    {"step_index": i, "selector": selector, "action": action},
                )
            else:
                ctx.bus.emit(
                    "EXECUTE", "STEP_FAIL",
                    f"Step {i + 1} failed ({action} on {selector!r})",
                    {
                        "step_index": i,
                        "selector": selector,
                        "action": action,
                        "healing_attempted": getattr(step, "healing_attempted", False),
                        "healing_result": "FAILED",
                        "screenshot_ref": getattr(step, "screenshot_path", None),
                        "console_errors": getattr(step, "console_errors", []),
                    },
                )
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/test_stage_execute.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/blop/engine/stages/execute.py tests/test_stage_execute.py
git commit -m "feat(stages): add ExecuteStage (Stage 3) with per-step event emission"
```

---

## Task 7: Stage 4 — CLASSIFY + Stage 5 — REPORT

**Files:**
- Create: `src/blop/engine/stages/classify.py`
- Create: `src/blop/engine/stages/report.py`
- Create: `tests/test_stage_classify_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage_classify_report.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from blop.engine.pipeline import RunContext
from blop.engine.stages.classify import ClassifyStage
from blop.engine.stages.report import ReportStage


def _ctx_with_cases():
    ctx = RunContext(run_id="r1", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    case = MagicMock()
    case.status = "failed"
    case.flow_id = "f1"
    case.case_id = "c1"
    ctx.step_results = [case]
    return ctx


@pytest.mark.asyncio
async def test_classify_stage_emits_events_and_stores_cases():
    ctx = _ctx_with_cases()
    classified = MagicMock()
    classified.failure_taxonomy = "GENUINE_REGRESSION"
    classified.severity = "BLOCKER"
    with patch("blop.engine.stages.classify.classify_run", new=AsyncMock(return_value=[classified])):
        await ClassifyStage().run(ctx)
    event_types = [e.event_type for e in ctx.bus.events]
    assert "CLASSIFY_START" in event_types
    assert "CLASSIFY_OK" in event_types
    assert len(ctx.classified_cases) == 1


@pytest.mark.asyncio
async def test_report_stage_emits_report_ready_with_decision():
    ctx = _ctx_with_cases()
    classified = MagicMock()
    classified.severity = "BLOCKER"
    ctx.classified_cases = [classified]
    with patch(
        "blop.engine.stages.report.build_report",
        return_value={"decision": "BLOCK", "blocker_count": 1, "total_cases": 1},
    ):
        await ReportStage().run(ctx)
    assert ctx.report["decision"] == "BLOCK"
    rr_events = [e for e in ctx.bus.events if e.event_type == "REPORT_READY"]
    assert len(rr_events) == 1
    assert rr_events[0].details["decision"] == "BLOCK"
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_stage_classify_report.py -v
```
Expected: FAIL

- [ ] **Step 3: Create `src/blop/engine/stages/classify.py`**

```python
"""Stage 4: CLASSIFY — score failures, assign taxonomy + severity."""
from __future__ import annotations

from typing import Any

from blop.engine.errors import BLOP_STAGE_CLASSIFY_FAILED, StageError
from blop.engine.pipeline import RunContext


async def classify_run(cases: list[Any]) -> list[Any]:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.engine.classifier import classify_run as _c
    return await _c(cases)


class ClassifyStage:
    async def run(self, ctx: RunContext) -> None:
        ctx.bus.emit(
            "CLASSIFY", "CLASSIFY_START",
            f"Classifying {len(ctx.step_results)} case(s)",
        )
        try:
            classified = await classify_run(ctx.step_results)
            ctx.classified_cases = classified
            blocker_count = sum(1 for c in classified if getattr(c, "severity", "") == "BLOCKER")
            ctx.bus.emit(
                "CLASSIFY", "CLASSIFY_OK",
                f"Classification complete: {blocker_count} blocker(s) of {len(classified)} case(s)",
                {"total_cases": len(classified), "blockers": blocker_count},
            )
        except Exception as exc:
            ctx.bus.emit("CLASSIFY", "CLASSIFY_FAIL", f"Classification failed: {exc}")
            raise StageError(
                stage="CLASSIFY",
                code=BLOP_STAGE_CLASSIFY_FAILED,
                message=f"Failure classification failed: {exc}",
                likely_cause="LLM API error during classification or malformed case data.",
                suggested_fix=(
                    "Check GOOGLE_API_KEY / BLOP_LLM_PROVIDER is set and the API is reachable. "
                    "Classification failures are usually transient — retry the run."
                ),
                retry_safe=True,
                details={"error": str(exc)},
            ) from exc
```

- [ ] **Step 4: Create `src/blop/engine/stages/report.py`**

```python
"""Stage 5: REPORT — produce SHIP / INVESTIGATE / BLOCK decision."""
from __future__ import annotations

from typing import Any

from blop.engine.errors import BLOP_STAGE_REPORT_FAILED, StageError
from blop.engine.pipeline import RunContext


def build_report(run_id: str, classified_cases: list[Any]) -> dict[str, Any]:
    """Thin wrapper so tests can patch at this module's import path."""
    from blop.reporting.results import build_report as _b
    return _b(run_id, classified_cases)


class ReportStage:
    async def run(self, ctx: RunContext) -> None:
        try:
            report = build_report(ctx.run_id, ctx.classified_cases)
            ctx.report = report
            decision = report.get("decision", "UNKNOWN")
            ctx.bus.emit(
                "REPORT", "REPORT_READY",
                f"Report ready — decision: {decision}",
                {
                    "decision": decision,
                    "run_id": ctx.run_id,
                    "blocker_count": report.get("blocker_count", 0),
                    "total_cases": report.get("total_cases", len(ctx.classified_cases)),
                },
            )
        except Exception as exc:
            raise StageError(
                stage="REPORT",
                code=BLOP_STAGE_REPORT_FAILED,
                message=f"Report generation failed: {exc}",
                likely_cause="Missing or malformed classified case data.",
                suggested_fix="Ensure CLASSIFY stage completed successfully, then retry.",
                retry_safe=True,
                details={"error": str(exc)},
            ) from exc
```

- [ ] **Step 5: Run to verify PASS**

```bash
pytest tests/test_stage_classify_report.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add src/blop/engine/stages/classify.py src/blop/engine/stages/report.py tests/test_stage_classify_report.py
git commit -m "feat(stages): add ClassifyStage (Stage 4) + ReportStage (Stage 5)"
```

---

## Task 8: Full pipeline integration test

**Files:**
- Create: `tests/test_pipeline_integration.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_pipeline_integration.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from blop.engine.errors import StageError
from blop.engine.pipeline import RunContext, build_default_pipeline


@pytest.mark.asyncio
async def test_full_pipeline_happy_path_emits_all_stage_events():
    ctx = RunContext(run_id="int_01", app_url="https://example.com", flow_ids=["f1"], profile_name=None)
    fake_case = MagicMock(status="passed", step_results=[], flow_id="f1", case_id="c1")
    fake_classified = MagicMock(severity="LOW", failure_taxonomy=None)

    with (
        patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com"),
        patch("blop.engine.stages.execute.run_flows", new=AsyncMock(return_value=[fake_case])),
        patch("blop.engine.stages.classify.classify_run", new=AsyncMock(return_value=[fake_classified])),
        patch(
            "blop.engine.stages.report.build_report",
            return_value={"decision": "SHIP", "blocker_count": 0, "total_cases": 1},
        ),
    ):
        await build_default_pipeline().run(ctx)

    assert ctx.report["decision"] == "SHIP"
    event_types = [e.event_type for e in ctx.bus.events]
    for expected in [
        "VALIDATE_START", "VALIDATE_OK",
        "AUTH_START", "AUTH_OK",
        "EXECUTE_START", "EXECUTE_DONE",
        "CLASSIFY_START", "CLASSIFY_OK",
        "REPORT_READY",
    ]:
        assert expected in event_types, f"Missing event: {expected}"
    assert "PIPELINE_ABORT" not in event_types


@pytest.mark.asyncio
async def test_full_pipeline_auth_failure_aborts_before_execute():
    ctx = RunContext(run_id="int_02", app_url="https://example.com", flow_ids=["f1"], profile_name="staging")

    with (
        patch("blop.engine.stages.validate.validate_app_url", return_value="https://example.com"),
        patch(
            "blop.engine.stages.auth.resolve_storage_state",
            new=AsyncMock(side_effect=Exception("profile missing")),
        ),
    ):
        with pytest.raises(StageError) as exc_info:
            await build_default_pipeline().run(ctx)

    assert exc_info.value.stage == "AUTH"
    assert "staging" in exc_info.value.suggested_fix
    event_types = [e.event_type for e in ctx.bus.events]
    assert "PIPELINE_ABORT" in event_types
    assert "EXECUTE_START" not in event_types
    abort = [e for e in ctx.bus.events if e.event_type == "PIPELINE_ABORT"][0]
    assert abort.details["suggested_fix"] != ""
```

- [ ] **Step 2: Run to verify PASS**

```bash
pytest tests/test_pipeline_integration.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_pipeline_integration.py
git commit -m "test(pipeline): add full integration tests for happy path + auth abort"
```

---

## Task 9: Expand ToolError with diagnostic fields

**Files:**
- Modify: `src/blop/mcp/envelope.py`
- Create: `tests/test_envelope_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_envelope_schema.py
from blop.mcp.envelope import ToolError, err_response, finalize_tool_payload


def test_tool_error_has_all_diagnostic_fields():
    err = ToolError(
        code="BLOP_AUTH_PROFILE_NOT_FOUND",
        message="Profile not found",
        likely_cause="Profile was never created",
        suggested_fix="Run save_auth_profile first",
        retry_safe=False,
        stage="AUTH",
    )
    assert err.likely_cause == "Profile was never created"
    assert err.suggested_fix == "Run save_auth_profile first"
    assert err.retry_safe is False
    assert err.stage == "AUTH"


def test_tool_error_diagnostic_fields_default_empty():
    err = ToolError(code="BLOP_VALIDATION_FAILED", message="bad")
    assert err.likely_cause == ""
    assert err.suggested_fix == ""
    assert err.retry_safe is False
    assert err.stage is None


def test_err_response_forwards_diagnostic_fields():
    resp = err_response(
        "BLOP_AUTH_PROFILE_NOT_FOUND",
        "Profile not found",
        likely_cause="not created",
        suggested_fix="create it",
        retry_safe=False,
        stage="AUTH",
    )
    assert resp.ok is False
    assert resp.error.likely_cause == "not created"
    assert resp.error.suggested_fix == "create it"
    assert resp.error.stage == "AUTH"


def test_finalize_propagates_diagnostic_fields_into_mcp_error():
    raw = {
        "ok": False,
        "error": {
            "code": "BLOP_AUTH_PROFILE_NOT_FOUND",
            "message": "Profile not found",
            "likely_cause": "never created",
            "suggested_fix": "create it",
            "retry_safe": False,
            "stage": "AUTH",
        },
    }
    result = finalize_tool_payload(raw, request_id="req_1", tool_name="run_regression_test")
    mcp_err = result.get("mcp_error") or {}
    details = mcp_err.get("details") or {}
    assert details.get("likely_cause") == "never created"
    assert details.get("suggested_fix") == "create it"
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_envelope_schema.py -v
```
Expected: 3-4 tests FAIL

- [ ] **Step 3: Modify `src/blop/mcp/envelope.py`**

Replace the `ToolError` class definition:
```python
class ToolError(BaseModel):
    code: str
    message: str
    likely_cause: str = ""
    suggested_fix: str = ""
    retry_safe: bool = False
    stage: str | None = None
    details: Any | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_detail_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "detail" in data and "details" not in data:
            data = {**data, "details": data.get("detail")}
        return data
```

Replace the `err_response` function:
```python
def err_response(
    code: str,
    message: str,
    detail: str | None = None,
    *,
    details: Any | None = None,
    likely_cause: str = "",
    suggested_fix: str = "",
    retry_safe: bool = False,
    stage: str | None = None,
) -> ToolResponse[Any]:
    merged_details: Any = details if details is not None else detail
    return ToolResponse(
        ok=False,
        data=None,
        error=ToolError(
            code=code,
            message=message,
            details=merged_details,
            likely_cause=likely_cause,
            suggested_fix=suggested_fix,
            retry_safe=retry_safe,
            stage=stage,
        ),
    )
```

In `finalize_tool_payload`, inside the `if raw.get("ok") is False:` branch, add diagnostic propagation immediately after `ed: dict[str, Any] = {}`:
```python
ed: dict[str, Any] = {}
if tool_name:
    ed["tool"] = tool_name
if terr.get("details") is not None:
    ed["atomic_details"] = terr.get("details")
# Propagate diagnostic fields so agents can act on them
if terr.get("likely_cause"):
    ed["likely_cause"] = terr["likely_cause"]
if terr.get("suggested_fix"):
    ed["suggested_fix"] = terr["suggested_fix"]
if terr.get("retry_safe") is not None:
    ed["retry_safe"] = terr["retry_safe"]
if terr.get("stage"):
    ed["stage"] = terr["stage"]
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/test_envelope_schema.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 5: Run full suite to catch regressions**

```bash
pytest tests/ -x -q --ignore=tests/e2e
```
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/blop/mcp/envelope.py tests/test_envelope_schema.py
git commit -m "feat(envelope): add likely_cause/suggested_fix/retry_safe/stage to ToolError"
```

---

## Task 10: Schema hardening — top 5 tools

**Files:**
- Modify: `src/blop/tools/regression.py`
- Modify: `src/blop/tools/evaluate.py`
- Modify: `src/blop/tools/journeys.py`
- Modify: `src/blop/tools/record.py`
- Modify: `src/blop/tools/triage.py`
- Create: `tests/test_schema_hardening.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_schema_hardening.py
import inspect

from blop.mcp.envelope import ToolError


def test_tool_error_has_all_required_fields():
    fields = ToolError.model_fields
    for f in ["code", "message", "likely_cause", "suggested_fix", "retry_safe", "stage", "details"]:
        assert f in fields, f"ToolError missing field: {f}"


def test_run_regression_test_mode_has_default_replay():
    import blop.tools.regression as mod
    # Find the callable with a 'mode' parameter
    for name in dir(mod):
        obj = getattr(mod, name)
        if callable(obj) and not name.startswith("_"):
            try:
                sig = inspect.signature(obj)
                if "mode" in sig.parameters:
                    assert sig.parameters["mode"].default == "replay", (
                        f"{name}.mode default must be 'replay'"
                    )
            except (ValueError, TypeError):
                pass


def test_business_criticality_valid_values():
    """Any tool accepting business_criticality must only allow the 5 canonical values."""
    valid = {"revenue", "activation", "retention", "support", "other"}
    import blop.tools.journeys as mod
    for name in dir(mod):
        obj = getattr(mod, name)
        if callable(obj) and not name.startswith("_"):
            try:
                sig = inspect.signature(obj)
                if "business_criticality" in sig.parameters:
                    ann = sig.parameters["business_criticality"].annotation
                    ann_str = str(ann)
                    for v in valid:
                        assert v in ann_str, f"business_criticality annotation missing '{v}': {ann_str}"
            except (ValueError, TypeError):
                pass
```

- [ ] **Step 2: Run to establish baseline**

```bash
pytest tests/test_schema_hardening.py -v
```
Note which tests pass and which fail before changes.

- [ ] **Step 3: Harden `src/blop/tools/regression.py`**

Add to imports at top of file (after existing imports):
```python
from typing import Annotated, Literal
from pydantic import Field
```

Find the `run_regression_test` function signature and update the `mode`, `flow_ids`, and `profile_name` parameters:

```python
# Find: mode: str = "replay",
# Replace with:
mode: Annotated[
    Literal["replay", "record"],
    Field(
        default="replay",
        description=(
            "replay: replays previously recorded steps deterministically. "
            "record: re-records all flows from scratch using the LLM agent. "
            "Use replay for release checks; use record to refresh flows after major UI changes."
        ),
    ),
] = "replay",

# Find: profile_name: ... = None,  (the str | None param)
# Replace with:
profile_name: Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Name of a saved auth profile created with save_auth_profile. "
            "Required for apps with login. Example: 'staging', 'prod_admin'."
        ),
        examples=["staging", "prod_admin"],
    ),
] = None,
```

- [ ] **Step 4: Harden `src/blop/tools/journeys.py`**

Add to imports:
```python
from typing import Annotated, Literal
from pydantic import Field
```

Find `discover_test_flows` or the main journey discovery function and update `business_criticality`:
```python
# Find any: business_criticality: str | None = None
# Replace with:
business_criticality: Annotated[
    Literal["revenue", "activation", "retention", "support", "other"] | None,
    Field(
        default=None,
        description=(
            "Primary business category for flows. "
            "revenue: checkout/billing/subscriptions. "
            "activation: onboarding/first-run. "
            "retention: core features users return for. "
            "support: help/error recovery. "
            "other: informational or low-stakes flows."
        ),
    ),
] = None,
```

- [ ] **Step 5: Harden `src/blop/tools/evaluate.py`**

Add to imports:
```python
from typing import Annotated
from pydantic import Field
```

Find `evaluate_web_task` and update `app_url` and `task`:
```python
# Update app_url:
app_url: Annotated[
    str,
    Field(
        description="Full URL of the page to evaluate. Must start with http:// or https://.",
        examples=["https://app.example.com/checkout", "http://localhost:3000"],
    ),
],

# Update task:
task: Annotated[
    str,
    Field(
        description=(
            "Natural language description of what to do and what to verify. "
            "Be specific: include the action, the page state to reach, and assertions to check."
        ),
        examples=[
            "Add the first product to cart and verify the cart badge shows '1'",
            "Log in with test@example.com and verify the dashboard loads without errors",
        ],
    ),
],
```

- [ ] **Step 6: Harden `src/blop/tools/record.py`**

Add to imports:
```python
from typing import Annotated, Literal
from pydantic import Field
```

Find `record_test_flow` and update `business_criticality` (same Literal as journeys above) and `goal`:
```python
goal: Annotated[
    str,
    Field(
        description="What the flow should accomplish. Used to guide the recording agent.",
        examples=[
            "Complete a purchase with a credit card",
            "Sign up for a new account and verify email confirmation is shown",
        ],
    ),
],
```

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/ -x -q --ignore=tests/e2e
```
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/blop/tools/regression.py src/blop/tools/evaluate.py src/blop/tools/journeys.py src/blop/tools/record.py src/blop/tools/triage.py tests/test_schema_hardening.py
git commit -m "feat(schema): harden tool parameters with Literal/Field descriptions + examples"
```

---

## Task 11: LLM_CALL_* event emission via contextvars

**Files:**
- Create: `src/blop/engine/llm_events.py`
- Create: `tests/test_llm_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_events.py
import pytest

from blop.engine.events import EventBus
from blop.engine.llm_events import (
    emit_llm_fail,
    emit_llm_fallback,
    emit_llm_ok,
    emit_llm_start,
    llm_event_bus,
    set_llm_event_bus,
)


def test_llm_events_emitted_when_bus_set():
    bus = EventBus("run_llm_01")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_start(provider="google", model="gemini-2.5-flash", call_id="c1")
        emit_llm_ok(provider="google", model="gemini-2.5-flash", call_id="c1")
    finally:
        llm_event_bus.reset(token)

    events = bus.events
    assert any(e.event_type == "LLM_CALL_START" for e in events)
    assert any(e.event_type == "LLM_CALL_OK" for e in events)
    start = next(e for e in events if e.event_type == "LLM_CALL_START")
    assert start.details["provider"] == "google"
    assert start.details["model"] == "gemini-2.5-flash"
    assert start.details["call_id"] == "c1"


def test_llm_events_noop_when_no_bus_set():
    # Must not raise even with no bus active
    emit_llm_start(provider="google", model="gemini-2.5-flash", call_id="c2")
    emit_llm_fail(provider="google", model="gemini-2.5-flash", call_id="c2", error="quota exceeded")


def test_llm_fail_event_has_error_field():
    bus = EventBus("run_llm_02")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_fail(provider="anthropic", model="claude-sonnet-4-6", call_id="c3", error="rate limit")
    finally:
        llm_event_bus.reset(token)
    fail_events = [e for e in bus.events if e.event_type == "LLM_CALL_FAIL"]
    assert len(fail_events) == 1
    assert fail_events[0].details["error"] == "rate limit"
    assert fail_events[0].details["provider"] == "anthropic"


def test_llm_fallback_event():
    bus = EventBus("run_llm_03")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_fallback(from_provider="google", to_provider="anthropic", reason="quota exceeded")
    finally:
        llm_event_bus.reset(token)
    fb = [e for e in bus.events if e.event_type == "LLM_CALL_FALLBACK"]
    assert len(fb) == 1
    assert fb[0].details["from_provider"] == "google"
    assert fb[0].details["to_provider"] == "anthropic"
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/test_llm_events.py -v
```
Expected: FAIL

- [ ] **Step 3: Create `src/blop/engine/llm_events.py`**

```python
"""Context-variable-based LLM_CALL_* event emission for pipeline runs.

Usage:
    token = set_llm_event_bus(ctx.bus)
    try:
        # ... pipeline stages run here ...
    finally:
        llm_event_bus.reset(token)

Any call to emit_llm_* within that context will attach events to the bus.
Safe to call when no bus is set — all functions are no-ops.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

from blop.engine.events import EventBus

llm_event_bus: ContextVar[Optional[EventBus]] = ContextVar("llm_event_bus", default=None)


def set_llm_event_bus(bus: EventBus):
    """Activate bus for LLM events in the current async context. Returns reset token."""
    return llm_event_bus.set(bus)


def _bus() -> EventBus | None:
    return llm_event_bus.get(None)


def emit_llm_start(*, provider: str, model: str, call_id: str | None = None) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE", "LLM_CALL_START",
        f"LLM call started: {provider}/{model}",
        {"provider": provider, "model": model, "call_id": call_id or str(uuid.uuid4())},
    )


def emit_llm_ok(*, provider: str, model: str, call_id: str | None = None) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE", "LLM_CALL_OK",
        f"LLM call succeeded: {provider}/{model}",
        {"provider": provider, "model": model, "call_id": call_id or ""},
    )


def emit_llm_fail(*, provider: str, model: str, call_id: str | None = None, error: str) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE", "LLM_CALL_FAIL",
        f"LLM call failed: {provider}/{model} — {error}",
        {"provider": provider, "model": model, "call_id": call_id or "", "error": error},
    )


def emit_llm_fallback(*, from_provider: str, to_provider: str, reason: str) -> None:
    bus = _bus()
    if bus is None:
        return
    bus.emit(
        "PIPELINE", "LLM_CALL_FALLBACK",
        f"LLM fallback: {from_provider} → {to_provider} ({reason})",
        {"from_provider": from_provider, "to_provider": to_provider, "reason": reason},
    )
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/test_llm_events.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/blop/engine/llm_events.py tests/test_llm_events.py
git commit -m "feat(llm): add LLM_CALL_* event emission via contextvars"
```

---

## Task 12: Full test suite gate

Before the live test, confirm all unit tests pass.

- [ ] **Step 1: Run all tests**

```bash
cd /Users/alejandrolaurlund/Development/blop-mcp
source .venv/bin/activate
pytest tests/ -q --ignore=tests/e2e 2>&1 | tail -20
```
Expected: All pass. If any fail, fix them before proceeding.

- [ ] **Step 2: Commit any fixes**

```bash
git add -p
git commit -m "fix: resolve test suite regressions from pipeline + schema changes"
```

---

## Task 13: Live end-to-end smoke test with .env credentials

**Files:**
- Create: `scripts/test_pipeline_live.py`

- [ ] **Step 1: Create the live smoke test script**

```python
#!/usr/bin/env python3
"""Live smoke test for the pipeline engine using .env credentials.

Usage:
    python scripts/test_pipeline_live.py

Requirements:
    - .env with GOOGLE_API_KEY (or BLOP_LLM_PROVIDER + matching key)
    - BLOP_APP_URL or APP_URL set to the target app

What it tests:
    1. evaluate_web_task round-trip (LLM + browser)
    2. ValidateStage + AuthStage directly (no browser)
    3. LLM_CALL_* event emission via contextvars
    4. ToolError diagnostic fields in finalize_tool_payload output
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load .env before any blop imports
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env, override=False)
    except ImportError:
        pass  # python-dotenv optional; rely on env already set


async def main() -> None:
    app_url = os.getenv("BLOP_APP_URL") or os.getenv("APP_URL")
    if not app_url:
        print("ERROR: BLOP_APP_URL not set in .env — set it to the target app URL")
        sys.exit(1)

    provider = os.getenv("BLOP_LLM_PROVIDER", "google")
    print(f"[smoke] app_url  : {app_url}")
    print(f"[smoke] provider : {provider}")

    # ── Check 1: validate + auth pipeline (no browser) ──────────────────────
    print("\n[1/4] ValidateStage + AuthStage (no browser required)")
    from blop.engine.pipeline import RunContext, RunPipeline
    from blop.engine.stages.auth import AuthStage
    from blop.engine.stages.validate import ValidateStage

    class NullStage:
        async def run(self, ctx: RunContext) -> None:
            pass

    mini = RunPipeline(
        validate=ValidateStage(),
        auth=AuthStage(),
        execute=NullStage(),
        classify=NullStage(),
        report=NullStage(),
    )
    ctx = RunContext(run_id="smoke_01", app_url=app_url, flow_ids=[], profile_name=None)
    await mini.run(ctx)
    print(f"     validated_url : {ctx.validated_url}")
    print(f"     auth_state    : {ctx.auth_state}")
    print("     Events:")
    for ev in ctx.bus.events:
        print(f"       [{ev.stage}] {ev.event_type}: {ev.message}")
    assert ctx.validated_url is not None, "FAIL: validated_url is None after ValidateStage"
    print("     PASS")

    # ── Check 2: LLM event bus ───────────────────────────────────────────────
    print("\n[2/4] LLM_CALL_* event emission")
    from blop.engine.events import EventBus
    from blop.engine.llm_events import emit_llm_fail, emit_llm_ok, emit_llm_start, llm_event_bus, set_llm_event_bus

    bus = EventBus("smoke_llm")
    token = set_llm_event_bus(bus)
    try:
        emit_llm_start(provider=provider, model="test-model", call_id="smoke_c1")
        emit_llm_ok(provider=provider, model="test-model", call_id="smoke_c1")
        emit_llm_fail(provider=provider, model="test-model", call_id="smoke_c2", error="simulated error")
    finally:
        llm_event_bus.reset(token)

    llm_evts = [e.event_type for e in bus.events]
    assert "LLM_CALL_START" in llm_evts
    assert "LLM_CALL_OK" in llm_evts
    assert "LLM_CALL_FAIL" in llm_evts
    print(f"     events : {llm_evts}")
    print("     PASS")

    # ── Check 3: ToolError diagnostic fields ─────────────────────────────────
    print("\n[3/4] ToolError diagnostic fields")
    from blop.mcp.envelope import err_response, finalize_tool_payload

    resp = err_response(
        "BLOP_AUTH_PROFILE_NOT_FOUND",
        "Profile 'staging' not found",
        likely_cause="Profile was never created",
        suggested_fix="Run save_auth_profile with profile_name='staging'",
        retry_safe=False,
        stage="AUTH",
    )
    assert resp.error.likely_cause == "Profile was never created"
    assert resp.error.suggested_fix == "Run save_auth_profile with profile_name='staging'"

    raw = resp.model_dump()
    finalized = finalize_tool_payload(
        {"ok": False, "error": raw["error"]},
        request_id="smoke_req_01",
        tool_name="run_regression_test",
    )
    mcp_details = (finalized.get("mcp_error") or {}).get("details") or {}
    assert mcp_details.get("likely_cause") == "Profile was never created", (
        f"likely_cause not propagated: {mcp_details}"
    )
    print(f"     likely_cause  : {mcp_details['likely_cause']}")
    print(f"     suggested_fix : {mcp_details.get('suggested_fix')}")
    print("     PASS")

    # ── Check 4: evaluate_web_task live LLM + browser round-trip ─────────────
    print(f"\n[4/4] evaluate_web_task live round-trip against {app_url}")
    from blop.tools.evaluate import evaluate_web_task

    result = await evaluate_web_task(
        app_url=app_url,
        task="Navigate to the homepage and verify the page title is not empty",
    )
    ok = result.get("ok", False)
    if ok:
        data = result.get("data") or {}
        decision = data.get("decision") or result.get("decision", "N/A")
        print(f"     ok={ok}  decision={decision}")
    else:
        mcp_err = result.get("mcp_error") or {}
        details = mcp_err.get("details") or {}
        print(f"     ok={ok}")
        print(f"     error         : {result.get('error')}")
        print(f"     likely_cause  : {details.get('likely_cause', 'N/A')}")
        print(f"     suggested_fix : {details.get('suggested_fix', 'N/A')}")
        # Non-fatal for smoke test — report but don't exit(1)
        print("     NOTE: live browser test failed (check error above)")
    print("     DONE")

    print("\n══════════════════════════════════════")
    print("  ALL SMOKE CHECKS COMPLETE")
    print("══════════════════════════════════════")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the live smoke test**

```bash
cd /Users/alejandrolaurlund/Development/blop-mcp
source .venv/bin/activate
python scripts/test_pipeline_live.py
```

Expected output:
```
[smoke] app_url  : https://...
[smoke] provider : google

[1/4] ValidateStage + AuthStage (no browser required)
     validated_url : https://...
     auth_state    : None
     Events:
       [VALIDATE] VALIDATE_START: Validating app_url: ...
       [VALIDATE] VALIDATE_OK: URL validated: ...
       [AUTH] AUTH_START: Resolving auth profile: (none)
       [AUTH] AUTH_OK: No auth profile required ...
     PASS

[2/4] LLM_CALL_* event emission
     events : ['LLM_CALL_START', 'LLM_CALL_OK', 'LLM_CALL_FAIL']
     PASS

[3/4] ToolError diagnostic fields
     likely_cause  : Profile was never created
     suggested_fix : Run save_auth_profile with profile_name='staging'
     PASS

[4/4] evaluate_web_task live round-trip against https://...
     ok=True  decision=...
     DONE

══════════════════════════════════════
  ALL SMOKE CHECKS COMPLETE
══════════════════════════════════════
```

- [ ] **Step 3: Commit**

```bash
git add scripts/test_pipeline_live.py
git commit -m "test(live): add pipeline + schema smoke test using .env credentials"
```

---

## Self-Review Checklist (completed)

- ✅ **Spec coverage:** All 5 stages (Tasks 4–7), HealthEvent taxonomy (Task 2), StageError (Task 1), canonical envelope (Task 9), LLM_CALL_* events (Task 11), schema hardening (Task 10), live test (Task 13).
- ✅ **Deferred clearly called out:** BrowserContextFactory, config split, server split, taxonomy unification — out of scope.
- ✅ **No placeholders:** All code blocks are complete. No TBD, TODO, or "similar to above."
- ✅ **Type consistency:** `RunContext` defined Task 3, used Tasks 4–8. `EventBus` Task 2, used throughout. `StageError` Task 1, raised in Tasks 4–7. `build_default_pipeline()` Task 3, used Tasks 8 and 13. `ToolError` diagnostic fields Task 9, verified Task 13. `llm_event_bus` ContextVar Task 11, used Task 13.
- ✅ **Each task tests before it implements (TDD order enforced).**
