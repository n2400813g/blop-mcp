from __future__ import annotations

import asyncio
import contextvars
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiosqlite

from blop.config import BLOP_DB_PATH
from blop.engine.errors import BLOP_STORAGE_DB_OPEN_FAILED, BLOP_STORAGE_MIGRATION_FAILED, BlopError
from blop.engine.logger import get_logger
from blop.schemas import (
    AuthProfile,
    FailureCase,
    IncidentCluster,
    RecordedFlow,
    ReleasePolicy,
    ReleaseSnapshot,
    RemediationDraft,
    SiteContextGraph,
    TelemetrySignal,
)

_log = get_logger("sqlite")

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})

_RUN_HEALTH_BUFFER_LIMIT = max(1, int(os.getenv("BLOP_RUN_HEALTH_BUFFER_LIMIT", "16")))
_RUN_HEALTH_HARD_MAX = max(
    _RUN_HEALTH_BUFFER_LIMIT,
    max(1, int(os.getenv("BLOP_EVENT_BUFFER_MAX", "64"))),
)
_ARTIFACT_BUFFER_LIMIT = max(1, int(os.getenv("BLOP_ARTIFACT_BUFFER_LIMIT", "24")))
_RUN_HEALTH_FLUSH_EVENTS = {
    "case_completed",
    "run_completed",
    "run_failed",
    "run_cancelled",
    "run_force_terminated",
}
_RUN_HEALTH_BUFFERS: dict[str, list[dict]] = {}
_ARTIFACT_BUFFERS: dict[str, list[dict]] = {}
_BUFFER_LOCK: asyncio.Lock | None = None


def _buffer_lock() -> asyncio.Lock:
    global _BUFFER_LOCK
    if _BUFFER_LOCK is None:
        _BUFFER_LOCK = asyncio.Lock()
    return _BUFFER_LOCK


def _db_path() -> str:
    return os.environ.get("BLOP_DB_PATH", BLOP_DB_PATH)


def _parse_run_duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at or not completed_at:
        return None
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        if e.tzinfo is None:
            e = e.replace(tzinfo=timezone.utc)
        return max(0.0, (e - s).total_seconds())
    except (TypeError, ValueError):
        return None


_shared_conn: aiosqlite.Connection | None = None
_conn_path: str | None = None
_conn_create_lock = asyncio.Lock()
_db_rw_lock = asyncio.Lock()
_db_connect_depth: contextvars.ContextVar[int] = contextvars.ContextVar("blop_sqlite_connect_depth", default=0)


async def _close_shared_unlocked() -> None:
    global _shared_conn, _conn_path
    if _shared_conn is not None:
        await _shared_conn.close()
    _shared_conn = None
    _conn_path = None


async def reset_db_connection() -> None:
    """Close the shared SQLite connection (tests or ``BLOP_DB_PATH`` change)."""
    async with _conn_create_lock:
        await _close_shared_unlocked()


async def _ensure_shared_connection() -> aiosqlite.Connection:
    global _shared_conn, _conn_path
    path = os.path.abspath(_db_path())
    if _shared_conn is not None and _conn_path == path:
        return _shared_conn
    async with _conn_create_lock:
        if _shared_conn is not None and _conn_path != path:
            await _close_shared_unlocked()
        if _shared_conn is None:
            os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
            try:
                _shared_conn = await aiosqlite.connect(path)
            except Exception as e:
                raise BlopError(
                    BLOP_STORAGE_DB_OPEN_FAILED,
                    f"Could not open SQLite database: {e}",
                    retryable=True,
                    details={"path": path, "error_type": type(e).__name__},
                ) from e
            await _shared_conn.execute("PRAGMA journal_mode=WAL")
            await _shared_conn.execute("PRAGMA synchronous=NORMAL")
            await _shared_conn.execute("PRAGMA foreign_keys=ON")
            await _shared_conn.commit()
            _conn_path = path
        return _shared_conn


@asynccontextmanager
async def _db_connect():
    """Serialize access to the shared connection; re-enter without re-acquiring the lock."""
    conn = await _ensure_shared_connection()
    depth = _db_connect_depth.get()
    if depth > 0:
        yield conn
        return
    token = _db_connect_depth.set(depth + 1)
    try:
        async with _db_rw_lock:
            yield conn
    finally:
        _db_connect_depth.reset(token)


async def init_db() -> None:
    path = _db_path()
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    async with _db_connect() as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auth_profiles (
                profile_name TEXT PRIMARY KEY,
                auth_type TEXT NOT NULL,
                config_json TEXT NOT NULL,
                storage_state_path TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                refreshed_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS recorded_flows (
                flow_id TEXT PRIMARY KEY,
                flow_name TEXT NOT NULL,
                app_url TEXT NOT NULL,
                goal TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                assertions_json TEXT,
                entry_url TEXT,
                spa_hints_json TEXT,
                run_mode_override TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                profile_name TEXT,
                flow_ids_json TEXT,
                status TEXT DEFAULT 'pending',
                started_at TEXT,
                completed_at TEXT,
                headless INTEGER DEFAULT 1,
                artifacts_dir TEXT,
                cases_json TEXT,
                run_mode TEXT DEFAULT 'hybrid'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS run_cases (
                case_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                flow_id TEXT NOT NULL,
                status TEXT,
                severity TEXT,
                result_json TEXT,
                replay_mode TEXT,
                step_failure_index INTEGER,
                assertion_failures_json TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                case_id TEXT,
                artifact_type TEXT,
                path TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS site_inventories (
                id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                crawled_at TEXT DEFAULT (datetime('now')),
                inventory_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS execution_plans (
                plan_id TEXT PRIMARY KEY,
                created_at TEXT DEFAULT (datetime('now')),
                intent_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS context_graphs (
                graph_id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                profile_name TEXT,
                archetype TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                graph_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS run_health_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS run_observations (
                run_id TEXT NOT NULL,
                observation_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (run_id, observation_key),
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS release_snapshots (
                release_id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                snapshot_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS incident_clusters (
                cluster_id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                first_seen TEXT,
                last_seen TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                cluster_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS remediation_drafts (
                cluster_id TEXT PRIMARY KEY,
                created_at TEXT DEFAULT (datetime('now')),
                draft_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_signals (
                signal_id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                source TEXT,
                ts TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                journey_key TEXT,
                route TEXT,
                value REAL NOT NULL,
                unit TEXT,
                tags_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS correlation_reports (
                report_id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                window TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                report_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS risk_calibration (
                record_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                app_url TEXT NOT NULL,
                predicted_decision TEXT NOT NULL,
                blocker_count INTEGER DEFAULT 0,
                critical_journey_failures INTEGER DEFAULT 0,
                flow_ids_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mobile_device_sessions (
                session_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                device_name TEXT,
                os_version TEXT,
                app_id TEXT,
                app_version TEXT,
                appium_session_id TEXT,
                started_at TEXT DEFAULT (datetime('now')),
                ended_at TEXT,
                status TEXT DEFAULT 'active',
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS release_policies (
                policy_id TEXT PRIMARY KEY,
                policy_name TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                is_default INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                repo_url TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Telemetry index for faster time-range queries
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ts_signals ON telemetry_signals(app_url, ts)")
        # Performance indexes on hot query paths
        await db.execute("CREATE INDEX IF NOT EXISTS idx_runs_app_status ON runs(app_url, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cases_run ON run_cases(run_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cases_flow ON run_cases(flow_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_health_run ON run_health_events(run_id)")
        await db.commit()

        # Migrate existing tables to add new columns if missing
        await _migrate(db)

        # Startup recovery: mark any runs orphaned in "running" state as "failed"
        # (happens when the server process was killed mid-run)
        await db.execute("UPDATE runs SET status = 'failed', completed_at = datetime('now') WHERE status = 'running'")
        await db.commit()


async def _migrate(db) -> None:
    """Add columns to existing tables that may predate schema additions.

    Each migration has a version number. Only migrations above the stored
    schema_version are applied, and the version is bumped after success.
    """
    _VERSIONED_MIGRATIONS: list[tuple[int, str, str, str]] = [
        # (version, table, column, col_type)
        (1, "recorded_flows", "assertions_json", "TEXT"),
        (2, "recorded_flows", "entry_url", "TEXT"),
        (3, "recorded_flows", "spa_hints_json", "TEXT"),
        (4, "recorded_flows", "business_criticality", "TEXT DEFAULT 'other'"),
        (5, "recorded_flows", "run_mode_override", "TEXT"),
        (6, "runs", "run_mode", "TEXT DEFAULT 'hybrid'"),
        (7, "run_cases", "replay_mode", "TEXT"),
        (8, "run_cases", "step_failure_index", "INTEGER"),
        (9, "run_cases", "assertion_failures_json", "TEXT"),
        (10, "run_cases", "business_criticality", "TEXT DEFAULT 'other'"),
        (11, "auth_profiles", "user_data_dir", "TEXT"),
        (12, "context_graphs", "profile_name", "TEXT"),
        (13, "context_graphs", "archetype", "TEXT"),
        (14, "run_cases", "healed_steps_json", "TEXT"),
        (15, "run_cases", "rerecorded", "INTEGER DEFAULT 0"),
        (16, "run_cases", "performance_metrics_json", "TEXT"),
        (17, "runs", "next_actions_json", "TEXT"),
        (18, "risk_calibration", "blocker_count", "INTEGER DEFAULT 0"),
        (19, "release_snapshots", "brief_json", "TEXT"),
        (20, "release_snapshots", "run_id", "TEXT"),
        (21, "recorded_flows", "intent_contract_json", "TEXT"),
        # Mobile App Testing (BLO-125)
        (22, "recorded_flows", "platform", "TEXT DEFAULT 'web'"),
        (23, "recorded_flows", "mobile_target_json", "TEXT"),
        (24, "runs", "platform", "TEXT DEFAULT 'web'"),
        (25, "run_cases", "device_log_path", "TEXT"),
        (26, "run_cases", "crash_report_path", "TEXT"),
        (27, "run_cases", "platform", "TEXT DEFAULT 'web'"),
        (28, "release_snapshots", "project_id", "TEXT"),
    ]

    current_version = await _get_schema_version(db)

    for version, table, column, col_type in _VERSIONED_MIGRATIONS:
        if version <= current_version:
            continue
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            await _set_schema_version(db, version)
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column name" in msg or "already exists" in msg:
                # Column already exists (e.g. re-run after partial failure): still advance
                await _set_schema_version(db, version)
            else:
                raise BlopError(
                    BLOP_STORAGE_MIGRATION_FAILED,
                    f"Schema migration {version} failed for {table}.{column}: {exc}",
                    retryable=False,
                    details={
                        "version": version,
                        "table": table,
                        "column": column,
                        "cause": type(exc).__name__,
                    },
                ) from exc

    await db.commit()


async def save_auth_profile(profile: AuthProfile, storage_state_path: str | None = None) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO auth_profiles
            (profile_name, auth_type, config_json, storage_state_path, refreshed_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                profile.profile_name,
                profile.auth_type,
                profile.model_dump_json(),
                storage_state_path,
            ),
        )
        await db.commit()


async def list_auth_profiles() -> list[dict]:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT profile_name, auth_type, storage_state_path, created_at, refreshed_at FROM auth_profiles ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "profile_name": row[0],
                    "auth_type": row[1],
                    "storage_state_path": row[2],
                    "created_at": row[3],
                    "refreshed_at": row[4],
                }
                for row in rows
            ]


async def get_auth_profile(profile_name: str) -> AuthProfile | None:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT config_json FROM auth_profiles WHERE profile_name = ?",
            (profile_name,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return AuthProfile.model_validate_json(row[0])
    return None


async def save_flow(flow: RecordedFlow) -> None:
    async with _db_connect() as db:
        mobile_target_json = None
        if getattr(flow, "mobile_target", None):
            mobile_target_json = flow.mobile_target.model_dump_json()
        steps_payload = [s.model_dump() for s in flow.steps]
        await db.execute(
            """
            INSERT OR REPLACE INTO recorded_flows
            (flow_id, flow_name, app_url, goal, steps_json, created_at, assertions_json, entry_url,
             spa_hints_json, business_criticality, run_mode_override, intent_contract_json,
             platform, mobile_target_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flow.flow_id,
                flow.flow_name,
                flow.app_url,
                flow.goal,
                json.dumps(steps_payload),
                flow.created_at,
                json.dumps(
                    {
                        "assertions": flow.assertions_json,
                        "api_expectations": [expectation.model_dump() for expectation in flow.api_expectations],
                    }
                ),
                flow.entry_url,
                flow.spa_hints.model_dump_json() if getattr(flow, "spa_hints", None) else None,
                flow.business_criticality,
                flow.run_mode_override,
                flow.intent_contract.model_dump_json() if getattr(flow, "intent_contract", None) else None,
                getattr(flow, "platform", "web"),
                mobile_target_json,
            ),
        )
        await db.commit()


_FLOW_FULL_SELECT = """SELECT flow_id, flow_name, app_url, goal, steps_json, created_at,
                          assertions_json, entry_url, spa_hints_json, business_criticality, run_mode_override,
                          intent_contract_json, platform, mobile_target_json
                   FROM recorded_flows"""
_FLOW_LEGACY_SELECT = """SELECT flow_id, flow_name, app_url, goal, steps_json, created_at,
                          assertions_json, entry_url, spa_hints_json, business_criticality, run_mode_override
                   FROM recorded_flows"""
_FLOW_SUMMARY_SELECT = (
    "SELECT flow_id, flow_name, app_url, goal, created_at, business_criticality, "
    "run_mode_override, entry_url, platform, intent_contract_json "
    "FROM recorded_flows"
)
_FLOW_SUMMARY_LEGACY_SELECT = (
    "SELECT flow_id, flow_name, app_url, goal, created_at, business_criticality, "
    "run_mode_override, entry_url, platform "
    "FROM recorded_flows"
)


def _ensure_flow_step_shape(step_payload: dict) -> dict:
    step_payload.setdefault("target_text", None)
    step_payload.setdefault("dom_fingerprint", None)
    step_payload.setdefault("url_before", None)
    step_payload.setdefault("url_after", None)
    step_payload.setdefault("screenshot_path", None)
    step_payload.setdefault("aria_role", None)
    step_payload.setdefault("aria_name", None)
    step_payload.setdefault("aria_snapshot", None)
    step_payload.setdefault("testid_selector", None)
    step_payload.setdefault("label_text", None)
    step_payload.setdefault("structured_assertion", None)
    if isinstance(step_payload.get("structured_assertion"), dict):
        step_payload["structured_assertion"].setdefault("semantic_query", None)
    step_payload.setdefault("mobile_selector", None)
    step_payload.setdefault("swipe_direction", None)
    step_payload.setdefault("swipe_distance_pct", None)
    step_payload.setdefault("touch_x_pct", None)
    step_payload.setdefault("touch_y_pct", None)
    step_payload.setdefault("pinch_scale", None)
    return step_payload


def _deserialize_flow_row(row) -> RecordedFlow:
    from blop.schemas import FlowStep, IntentContract, MobileDeviceTarget, SpaHints

    steps_data = json.loads(row[4]) if row[4] else []
    steps = [FlowStep(**_ensure_flow_step_shape(dict(step_payload))) for step_payload in steps_data]

    assertions_json: list[str] = []
    api_expectations: list = []
    if row[6]:
        try:
            parsed_assertions = json.loads(row[6])
            if isinstance(parsed_assertions, dict):
                assertions_json = list(parsed_assertions.get("assertions", []) or [])
                api_expectations = list(parsed_assertions.get("api_expectations", []) or [])
            elif isinstance(parsed_assertions, list):
                assertions_json = parsed_assertions
        except Exception:
            _log.debug("failed to parse assertions_json for flow", exc_info=True)

    spa_hints = SpaHints()
    if row[8]:
        try:
            spa_hints = SpaHints.model_validate_json(row[8])
        except Exception:
            _log.debug("failed to parse spa_hints for flow", exc_info=True)

    intent_contract = None
    if len(row) > 11 and row[11]:
        try:
            intent_contract = IntentContract.model_validate_json(row[11])
        except Exception:
            _log.debug("failed to parse intent_contract_json for flow", exc_info=True)

    platform = "web"
    if len(row) > 12 and row[12]:
        platform = row[12]

    mobile_target = None
    if len(row) > 13 and row[13]:
        try:
            mobile_target = MobileDeviceTarget.model_validate_json(row[13])
        except Exception:
            _log.debug("failed to parse mobile_target_json for flow", exc_info=True)

    return RecordedFlow(
        flow_id=row[0],
        flow_name=row[1],
        app_url=row[2],
        goal=row[3],
        steps=steps,
        created_at=row[5],
        assertions_json=assertions_json,
        api_expectations=api_expectations,
        entry_url=row[7],
        spa_hints=spa_hints,
        business_criticality=row[9] or "other",
        run_mode_override=row[10] if len(row) > 10 else None,
        intent_contract=intent_contract,
        platform=platform,
        mobile_target=mobile_target,
    )


def _flow_summary_from_row(row) -> dict:
    return {
        "flow_id": row[0],
        "flow_name": row[1],
        "app_url": row[2],
        "goal": row[3],
        "created_at": row[4],
        "business_criticality": row[5] or "other",
        "run_mode_override": row[6] if len(row) > 6 else None,
        "entry_url": row[7] if len(row) > 7 else None,
        "platform": row[8] if len(row) > 8 and row[8] else "web",
        "has_intent_contract": bool(row[9]) if len(row) > 9 else False,
    }


async def get_flow(flow_id: str) -> RecordedFlow | None:
    async with _db_connect() as db:
        row = None
        try:
            async with db.execute(
                f"{_FLOW_FULL_SELECT} WHERE flow_id = ?",
                (flow_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except Exception as exc:
            if (
                "intent_contract_json" not in str(exc)
                and "platform" not in str(exc)
                and "mobile_target_json" not in str(exc)
            ):
                raise
            # Fallback for pre-migration databases
            async with db.execute(
                f"{_FLOW_LEGACY_SELECT} WHERE flow_id = ?",
                (flow_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row:
            return _deserialize_flow_row(row)
    return None


async def list_flows() -> list[dict]:
    async with _db_connect() as db:
        rows = []
        try:
            async with db.execute(f"{_FLOW_SUMMARY_SELECT} ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
        except Exception as exc:
            if "intent_contract_json" not in str(exc) and "platform" not in str(exc):
                raise
            async with db.execute(f"{_FLOW_SUMMARY_LEGACY_SELECT} ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
        return [_flow_summary_from_row(r) for r in rows]


async def list_flows_full(
    app_url: str | None = None,
    criticality_filter: list[str] | None = None,
) -> list[RecordedFlow]:
    clauses: list[str] = []
    params: list[object] = []
    if app_url:
        clauses.append("app_url = ?")
        params.append(app_url)
    if criticality_filter:
        deduped = [value for value in dict.fromkeys(criticality_filter) if value]
        if deduped:
            placeholders = ",".join("?" for _ in deduped)
            clauses.append(f"business_criticality IN ({placeholders})")
            params.extend(deduped)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    async with _db_connect() as db:
        rows = []
        try:
            async with db.execute(
                f"{_FLOW_FULL_SELECT}{where_sql} ORDER BY created_at DESC",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        except Exception as exc:
            if (
                "intent_contract_json" not in str(exc)
                and "platform" not in str(exc)
                and "mobile_target_json" not in str(exc)
            ):
                raise
            async with db.execute(
                f"{_FLOW_LEGACY_SELECT}{where_sql} ORDER BY created_at DESC",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
    return [_deserialize_flow_row(row) for row in rows]


async def get_flows(flow_ids: list[str]) -> list[RecordedFlow]:
    if not flow_ids:
        return []
    ordered_ids = [flow_id for flow_id in flow_ids if isinstance(flow_id, str) and flow_id.strip()]
    if not ordered_ids:
        return []
    placeholders = ",".join("?" for _ in ordered_ids)
    flow_map: dict[str, RecordedFlow] = {}
    async with _db_connect() as db:
        rows = []
        try:
            async with db.execute(
                f"{_FLOW_FULL_SELECT} WHERE flow_id IN ({placeholders})",
                ordered_ids,
            ) as cursor:
                rows = await cursor.fetchall()
        except Exception as exc:
            if (
                "intent_contract_json" not in str(exc)
                and "platform" not in str(exc)
                and "mobile_target_json" not in str(exc)
            ):
                raise
            async with db.execute(
                f"{_FLOW_LEGACY_SELECT} WHERE flow_id IN ({placeholders})",
                ordered_ids,
            ) as cursor:
                rows = await cursor.fetchall()
    for row in rows:
        flow = _deserialize_flow_row(row)
        flow_map[flow.flow_id] = flow
    return [flow_map[flow_id] for flow_id in ordered_ids if flow_id in flow_map]


async def find_flow_by_url_and_name(app_url: str, flow_name: str) -> dict | None:
    """Return the first recorded flow matching *app_url* and *flow_name*, or None."""
    async with _db_connect() as db:
        try:
            async with db.execute(
                "SELECT flow_id, flow_name, app_url, goal, created_at, run_mode_override, intent_contract_json "
                "FROM recorded_flows WHERE app_url = ? AND flow_name = ? LIMIT 1",
                (app_url, flow_name),
            ) as cursor:
                row = await cursor.fetchone()
        except Exception as exc:
            if "intent_contract_json" not in str(exc):
                raise
            async with db.execute(
                "SELECT flow_id, flow_name, app_url, goal, created_at, run_mode_override "
                "FROM recorded_flows WHERE app_url = ? AND flow_name = ? LIMIT 1",
                (app_url, flow_name),
            ) as cursor:
                row = await cursor.fetchone()
    if not row:
        return None
    return {
        "flow_id": row[0],
        "flow_name": row[1],
        "app_url": row[2],
        "goal": row[3],
        "created_at": row[4],
        "run_mode_override": row[5] if len(row) > 5 else None,
        "has_intent_contract": bool(row[6]) if len(row) > 6 else False,
    }


async def create_run(
    run_id: str,
    app_url: str,
    profile_name: str | None,
    flow_ids: list[str],
    headless: bool,
    artifacts_dir: str,
    run_mode: str = "hybrid",
) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT INTO runs (run_id, app_url, profile_name, flow_ids_json, status, started_at, headless, artifacts_dir, run_mode)
            VALUES (?, ?, ?, ?, 'queued', datetime('now'), ?, ?, ?)
            """,
            (
                run_id,
                app_url,
                profile_name,
                json.dumps(flow_ids),
                1 if headless else 0,
                artifacts_dir,
                run_mode,
            ),
        )
        await db.commit()
    from blop.engine import metrics as blop_metrics

    blop_metrics.inc_active_run()


async def create_run_with_initial_events(
    run_id: str,
    app_url: str,
    profile_name: str | None,
    flow_ids: list[str],
    headless: bool,
    artifacts_dir: str,
    run_mode: str,
    status: str,
    run_queued_payload: dict,
    auth_context_payload: dict,
) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT INTO runs (run_id, app_url, profile_name, flow_ids_json, status, started_at, headless, artifacts_dir, run_mode)
            VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)
            """,
            (
                run_id,
                app_url,
                profile_name,
                json.dumps(flow_ids),
                status,
                1 if headless else 0,
                artifacts_dir,
                run_mode,
            ),
        )
        await db.execute(
            """
            INSERT INTO run_health_events (event_id, run_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                "run_queued",
                json.dumps(run_queued_payload),
            ),
        )
        await db.execute(
            """
            INSERT INTO run_health_events (event_id, run_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                "auth_context_resolved",
                json.dumps(auth_context_payload),
            ),
        )
        await db.commit()
    from blop.engine import metrics as blop_metrics

    blop_metrics.inc_active_run()


async def update_run_status(run_id: str, status: str) -> None:
    """Update only the status of a run (lightweight state-machine transition)."""
    duration_sec: float | None = None
    already_terminal = False
    async with _db_connect() as db:
        async with db.execute(
            "SELECT status, started_at, completed_at FROM runs WHERE run_id = ?",
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
        prev = row[0] if row else None
        already_terminal = bool(prev and prev in _TERMINAL_RUN_STATUSES)
        if status in _TERMINAL_RUN_STATUSES and not already_terminal and row:
            duration_sec = _parse_run_duration_seconds(row[1], row[2])
        await db.execute(
            "UPDATE runs SET status = ? WHERE run_id = ?",
            (status, run_id),
        )
        await db.commit()
    if status in _TERMINAL_RUN_STATUSES:
        from blop.engine import metrics as blop_metrics

        blop_metrics.record_run_terminal(
            status=status,
            duration_seconds=duration_sec,
            already_terminal=already_terminal,
        )


async def update_run(
    run_id: str,
    status: str,
    cases: list[FailureCase],
    completed_at: str | None = None,
    next_actions: list[str] | None = None,
) -> None:
    duration_sec: float | None = None
    already_terminal = False
    async with _db_connect() as db:
        async with db.execute("SELECT status, started_at FROM runs WHERE run_id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
        prev = row[0] if row else None
        already_terminal = bool(prev and prev in _TERMINAL_RUN_STATUSES)
        started = row[1] if row else None
        if status in _TERMINAL_RUN_STATUSES and not already_terminal and started and completed_at:
            duration_sec = _parse_run_duration_seconds(started, completed_at)
        await db.execute(
            """
            UPDATE runs SET status = ?, cases_json = ?, completed_at = ?, next_actions_json = ?
            WHERE run_id = ?
            """,
            (
                status,
                json.dumps([c.model_dump() for c in cases]),
                completed_at,
                json.dumps(next_actions) if next_actions else None,
                run_id,
            ),
        )
        await db.commit()
    if status in _TERMINAL_RUN_STATUSES:
        from blop.engine import metrics as blop_metrics

        blop_metrics.record_run_terminal(
            status=status,
            duration_seconds=duration_sec,
            already_terminal=already_terminal,
        )


async def get_run(run_id: str) -> dict | None:
    async with _db_connect() as db:
        async with db.execute(
            """SELECT run_id, app_url, profile_name, flow_ids_json, status,
                      started_at, completed_at, headless, artifacts_dir, cases_json, run_mode,
                      next_actions_json
               FROM runs WHERE run_id = ?""",
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                next_actions: list[str] = []
                if row[11]:
                    try:
                        next_actions = json.loads(row[11])
                    except Exception:
                        _log.debug("failed to parse next_actions_json for run", exc_info=True)
                return {
                    "run_id": row[0],
                    "app_url": row[1],
                    "profile_name": row[2],
                    "flow_ids": json.loads(row[3]) if row[3] else [],
                    "status": row[4],
                    "started_at": row[5],
                    "completed_at": row[6],
                    "headless": bool(row[7]),
                    "artifacts_dir": row[8] or "",
                    "cases": json.loads(row[9]) if row[9] else [],
                    "run_mode": row[10] or "hybrid",
                    "next_actions": next_actions,
                }
    return None


_VALID_STATUSES = {"queued", "running", "completed", "failed", "cancelled", "waiting_auth"}


async def list_runs(limit: int = 20, status: str | None = None) -> list[dict]:
    if status is not None and status not in _VALID_STATUSES:
        return []
    safe_limit = max(1, min(limit, 200))
    async with _db_connect() as db:
        if status:
            query = """SELECT run_id, app_url, profile_name, status, started_at, completed_at,
                              headless, artifacts_dir, run_mode
                       FROM runs
                       WHERE status = ?
                       ORDER BY started_at DESC
                       LIMIT ?"""
            params = (status, safe_limit)
        else:
            query = """SELECT run_id, app_url, profile_name, status, started_at, completed_at,
                              headless, artifacts_dir, run_mode
                       FROM runs
                       ORDER BY started_at DESC
                       LIMIT ?"""
            params = (safe_limit,)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "run_id": row[0],
                    "app_url": row[1],
                    "profile_name": row[2],
                    "status": row[3],
                    "started_at": row[4],
                    "completed_at": row[5],
                    "headless": bool(row[6]),
                    "artifacts_dir": row[7] or "",
                    "run_mode": row[8] or "hybrid",
                }
                for row in rows
            ]


def _case_insert_row(case: FailureCase) -> tuple:
    return (
        case.case_id,
        case.run_id,
        case.flow_id,
        case.status,
        case.severity,
        case.model_dump_json(),
        case.replay_mode,
        case.step_failure_index,
        json.dumps(case.assertion_failures),
        case.business_criticality,
        json.dumps([h.model_dump() for h in case.healed_steps]) if case.healed_steps else None,
        1 if case.rerecorded else 0,
    )


async def save_case(case: FailureCase) -> None:
    await save_cases([case])


async def save_cases(cases: list[FailureCase]) -> None:
    if not cases:
        return
    async with _db_connect() as db:
        await db.executemany(
            """
            INSERT OR REPLACE INTO run_cases
            (case_id, run_id, flow_id, status, severity, result_json,
             replay_mode, step_failure_index, assertion_failures_json, business_criticality,
             healed_steps_json, rerecorded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [_case_insert_row(case) for case in cases],
        )
        await db.commit()


async def list_cases_for_run(run_id: str) -> list[FailureCase]:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT result_json FROM run_cases WHERE run_id = ?",
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            cases = []
            for row in rows:
                try:
                    cases.append(FailureCase.model_validate_json(row[0]))
                except Exception:
                    _log.debug("failed to parse result_json for run case", exc_info=True)
            return cases


async def list_cases_for_runs(run_ids: list[str]) -> dict[str, list[FailureCase]]:
    if not run_ids:
        return {}
    ordered_run_ids = [run_id for run_id in run_ids if isinstance(run_id, str) and run_id.strip()]
    if not ordered_run_ids:
        return {}
    placeholders = ",".join("?" for _ in ordered_run_ids)
    grouped: dict[str, list[FailureCase]] = {run_id: [] for run_id in ordered_run_ids}
    async with _db_connect() as db:
        async with db.execute(
            f"SELECT run_id, result_json FROM run_cases WHERE run_id IN ({placeholders}) ORDER BY rowid ASC",
            ordered_run_ids,
        ) as cursor:
            rows = await cursor.fetchall()
            for run_id, payload in rows:
                try:
                    grouped.setdefault(run_id, []).append(FailureCase.model_validate_json(payload))
                except Exception:
                    _log.debug("failed to parse result_json for batched run cases", exc_info=True)
    return grouped


async def get_case(case_id: str) -> FailureCase | None:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT result_json FROM run_cases WHERE case_id = ?",
            (case_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                return FailureCase.model_validate_json(row[0])
            except Exception:
                return None


async def _write_artifacts(records: list[dict]) -> None:
    if not records:
        return
    async with _db_connect() as db:
        await db.executemany(
            """
            INSERT INTO artifacts (artifact_id, run_id, case_id, artifact_type, path)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    record.get("artifact_id") or str(uuid.uuid4()),
                    record["run_id"],
                    record.get("case_id"),
                    record["artifact_type"],
                    record["path"],
                )
                for record in records
            ],
        )
        await db.commit()


async def save_artifacts(records: list[dict]) -> None:
    if not records:
        return
    async with _buffer_lock():
        grouped: dict[str, list[dict]] = {}
        for record in records:
            run_id = str(record["run_id"])
            grouped.setdefault(run_id, []).append(
                {
                    "artifact_id": record.get("artifact_id") or str(uuid.uuid4()),
                    "run_id": run_id,
                    "case_id": record.get("case_id"),
                    "artifact_type": record["artifact_type"],
                    "path": record["path"],
                }
            )
        for run_id, items in grouped.items():
            _ARTIFACT_BUFFERS.setdefault(run_id, []).extend(items)
    await flush_buffered_writes(run_ids=list(grouped))


async def save_artifact(run_id: str, case_id: str | None, artifact_type: str, path: str) -> None:
    should_flush = False
    async with _buffer_lock():
        buffer = _ARTIFACT_BUFFERS.setdefault(run_id, [])
        buffer.append(
            {
                "artifact_id": str(uuid.uuid4()),
                "run_id": run_id,
                "case_id": case_id,
                "artifact_type": artifact_type,
                "path": path,
            }
        )
        should_flush = len(buffer) >= _ARTIFACT_BUFFER_LIMIT
    if should_flush:
        await flush_buffered_writes(run_id=run_id)


async def save_site_inventory(app_url: str, inventory_dict: dict) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT INTO site_inventories (id, app_url, inventory_json)
            VALUES (?, ?, ?)
            """,
            (str(uuid.uuid4()), app_url, json.dumps(inventory_dict)),
        )
        await db.commit()


async def get_latest_site_inventory(app_url: str) -> dict | None:
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT id, app_url, crawled_at, inventory_json
            FROM site_inventories
            WHERE app_url = ?
            ORDER BY crawled_at DESC
            LIMIT 1
            """,
            (app_url,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                inventory = json.loads(row[3])
            except Exception:
                inventory = {}
            return {
                "id": row[0],
                "app_url": row[1],
                "crawled_at": row[2],
                "inventory": inventory,
            }


async def list_artifacts_for_run(run_id: str) -> list[dict]:
    await flush_buffered_writes(run_id=run_id)
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT artifact_id, run_id, case_id, artifact_type, path, created_at
            FROM artifacts
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "artifact_id": row[0],
                    "run_id": row[1],
                    "case_id": row[2],
                    "artifact_type": row[3],
                    "path": row[4],
                    "created_at": row[5],
                }
                for row in rows
            ]


async def update_flow_step_selector(flow_id: str, step_id: int, updates: dict) -> bool:
    """Persist healed selector fields back into a recorded flow's step.

    ``updates`` may contain keys like ``selector``, ``aria_role``, ``aria_name``,
    ``testid_selector``, ``label_text``, or ``target_text``.
    Returns True if the flow was found and updated.
    """

    async with _db_connect() as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT steps_json FROM recorded_flows WHERE flow_id = ?",
                (flow_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    await db.rollback()
                    return False

            steps_data = json.loads(row[0])
            changed = False
            for s in steps_data:
                if s.get("step_id") == step_id:
                    for key, val in updates.items():
                        if val is not None:
                            s[key] = val
                            changed = True
                    break

            if not changed:
                await db.rollback()
                return False

            await db.execute(
                "UPDATE recorded_flows SET steps_json = ? WHERE flow_id = ?",
                (json.dumps(steps_data), flow_id),
            )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def list_cases_for_flow(flow_id: str, limit: int = 50) -> list[FailureCase]:
    safe_limit = max(1, min(limit, 500))
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT result_json
            FROM run_cases
            WHERE flow_id = ?
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (flow_id, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            cases: list[FailureCase] = []
            for row in rows:
                try:
                    cases.append(FailureCase.model_validate_json(row[0]))
                except Exception:
                    _log.debug("failed to parse result_json for flow case", exc_info=True)
            return cases


async def save_context_graph(graph: SiteContextGraph) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO context_graphs
            (graph_id, app_url, profile_name, archetype, created_at, graph_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                graph.graph_id,
                graph.app_url,
                graph.profile_name,
                graph.archetype,
                graph.created_at,
                graph.model_dump_json(),
            ),
        )
        await db.commit()


async def get_latest_context_graph(app_url: str, profile_name: str | None = None) -> SiteContextGraph | None:
    async with _db_connect() as db:
        if profile_name:
            query = """
                SELECT graph_json
                FROM context_graphs
                WHERE app_url = ? AND profile_name = ?
                ORDER BY created_at DESC
                LIMIT 1
            """
            params = (app_url, profile_name)
        else:
            query = """
                SELECT graph_json
                FROM context_graphs
                WHERE app_url = ?
                ORDER BY created_at DESC
                LIMIT 1
            """
            params = (app_url,)

        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    return SiteContextGraph.model_validate_json(row[0])
                except Exception:
                    return None
    return None


async def get_context_graph(graph_id: str) -> SiteContextGraph | None:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT graph_json FROM context_graphs WHERE graph_id = ?",
            (graph_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    return SiteContextGraph.model_validate_json(row[0])
                except Exception:
                    return None
    return None


async def list_context_graphs(app_url: str, limit: int = 5) -> list[dict]:
    safe_limit = max(1, min(limit, 100))
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT graph_id, app_url, profile_name, archetype, created_at
            FROM context_graphs
            WHERE app_url = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (app_url, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "graph_id": row[0],
                    "app_url": row[1],
                    "profile_name": row[2],
                    "archetype": row[3],
                    "created_at": row[4],
                }
                for row in rows
            ]


async def upsert_run_observation(run_id: str, observation_key: str, payload: dict) -> None:
    """Idempotent agent observation: same (run_id, observation_key) overwrites payload."""
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT INTO run_observations (run_id, observation_key, payload_json, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(run_id, observation_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = datetime('now')
            """,
            (run_id, observation_key, json.dumps(payload)),
        )
        await db.commit()


async def get_run_observation(run_id: str, observation_key: str) -> dict | None:
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT payload_json, updated_at
            FROM run_observations
            WHERE run_id = ? AND observation_key = ?
            """,
            (run_id, observation_key),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            payload = json.loads(row[0]) if row[0] else {}
            payload["updated_at"] = row[1]
            return payload


async def _write_run_health_events(events: list[dict]) -> None:
    if not events:
        return
    async with _db_connect() as db:
        await db.executemany(
            """
            INSERT INTO run_health_events (event_id, run_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    event.get("event_id") or str(uuid.uuid4()),
                    event["run_id"],
                    event["event_type"],
                    json.dumps(event.get("payload", {})),
                )
                for event in events
            ],
        )
        await db.commit()


async def flush_buffered_writes(
    run_id: str | None = None,
    *,
    run_ids: list[str] | None = None,
) -> None:
    flush_ids: list[str] = []
    if run_ids:
        flush_ids.extend(run_ids)
    if run_id:
        flush_ids.append(run_id)
    if not flush_ids:
        async with _buffer_lock():
            flush_ids = list(dict.fromkeys([*_RUN_HEALTH_BUFFERS.keys(), *_ARTIFACT_BUFFERS.keys()]))
    if not flush_ids:
        return

    async with _buffer_lock():
        normalized_ids = list(dict.fromkeys(flush_ids))
        event_batches = {rid: _RUN_HEALTH_BUFFERS.pop(rid, []) for rid in normalized_ids}
        artifact_batches = {rid: _ARTIFACT_BUFFERS.pop(rid, []) for rid in normalized_ids}

    for rid in normalized_ids:
        if event_batches.get(rid):
            await _write_run_health_events(event_batches[rid])
        if artifact_batches.get(rid):
            await _write_artifacts(artifact_batches[rid])


async def save_run_health_event(run_id: str, event_type: str, payload: dict) -> None:
    should_flush = False
    async with _buffer_lock():
        buffer = _RUN_HEALTH_BUFFERS.setdefault(run_id, [])
        buffer.append(
            {
                "event_id": str(uuid.uuid4()),
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
            }
        )
        should_flush = (
            len(buffer) >= _RUN_HEALTH_BUFFER_LIMIT
            or len(buffer) >= _RUN_HEALTH_HARD_MAX
            or event_type in _RUN_HEALTH_FLUSH_EVENTS
        )
    if should_flush:
        await flush_buffered_writes(run_id=run_id)


async def save_run_health_events(events: list[dict]) -> None:
    if not events:
        return
    async with _buffer_lock():
        grouped: dict[str, list[dict]] = {}
        for event in events:
            run_id = str(event["run_id"])
            grouped.setdefault(run_id, []).append(
                {
                    "event_id": event.get("event_id") or str(uuid.uuid4()),
                    "run_id": run_id,
                    "event_type": event["event_type"],
                    "payload": event.get("payload", {}),
                }
            )
            if event["event_type"] in _RUN_HEALTH_FLUSH_EVENTS:
                grouped[run_id][-1]["_flush_now"] = True
        for run_id, items in grouped.items():
            _RUN_HEALTH_BUFFERS.setdefault(run_id, []).extend(items)
    await flush_buffered_writes(run_ids=list(grouped))


async def list_run_health_events(run_id: str, limit: int = 500) -> list[dict]:
    safe_limit = max(1, min(limit, 2000))
    await flush_buffered_writes(run_id=run_id)
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT event_id, run_id, event_type, payload_json, created_at
            FROM run_health_events
            WHERE run_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (run_id, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            out: list[dict] = []
            for row in rows:
                try:
                    payload = json.loads(row[3])
                except Exception:
                    payload = {}
                out.append(
                    {
                        "event_id": row[0],
                        "run_id": row[1],
                        "event_type": row[2],
                        "payload": payload,
                        "created_at": row[4],
                    }
                )
            return out


async def save_release_snapshot(snapshot: ReleaseSnapshot) -> None:
    """Persist graph/context snapshot; preserves brief_json, run_id, and project_id if row exists."""
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT brief_json, run_id, project_id FROM release_snapshots WHERE release_id = ?
            """,
            (snapshot.release_id,),
        ) as cursor:
            row = await cursor.fetchone()
        brief_json = row[0] if row else None
        run_id = row[1] if row else None
        project_id = row[2] if row else None
        await db.execute(
            """
            INSERT OR REPLACE INTO release_snapshots
            (release_id, app_url, created_at, snapshot_json, brief_json, run_id, project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.release_id,
                snapshot.app_url,
                snapshot.created_at,
                snapshot.model_dump_json(),
                brief_json,
                run_id,
                project_id,
            ),
        )
        await db.commit()


async def get_release_snapshot(release_id: str) -> ReleaseSnapshot | None:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT snapshot_json FROM release_snapshots WHERE release_id = ?",
            (release_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                return ReleaseSnapshot.model_validate_json(row[0])
            except Exception:
                return None


async def list_release_snapshots(app_url: str, limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(limit, 200))
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT release_id, app_url, created_at
            FROM release_snapshots
            WHERE app_url = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (app_url, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "release_id": row[0],
                    "app_url": row[1],
                    "created_at": row[2],
                }
                for row in rows
            ]


async def save_incident_cluster(cluster: IncidentCluster) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO incident_clusters
            (cluster_id, app_url, status, first_seen, last_seen, cluster_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cluster.cluster_id,
                cluster.app_url,
                cluster.status,
                cluster.first_seen,
                cluster.last_seen,
                cluster.model_dump_json(),
            ),
        )
        await db.commit()


async def get_incident_cluster(cluster_id: str) -> IncidentCluster | None:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT cluster_json FROM incident_clusters WHERE cluster_id = ?",
            (cluster_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                return IncidentCluster.model_validate_json(row[0])
            except Exception:
                return None


async def list_open_incident_clusters(app_url: str, limit: int = 100) -> list[IncidentCluster]:
    safe_limit = max(1, min(limit, 500))
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT cluster_json
            FROM incident_clusters
            WHERE app_url = ? AND status = 'open'
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (app_url, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            clusters: list[IncidentCluster] = []
            for row in rows:
                try:
                    clusters.append(IncidentCluster.model_validate_json(row[0]))
                except Exception:
                    _log.debug("failed to parse cluster_json for incident cluster", exc_info=True)
            return clusters


async def save_remediation_draft(draft: RemediationDraft) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO remediation_drafts
            (cluster_id, created_at, draft_json)
            VALUES (?, ?, ?)
            """,
            (
                draft.cluster_id,
                draft.created_at,
                draft.model_dump_json(),
            ),
        )
        await db.commit()


async def get_remediation_draft(cluster_id: str) -> RemediationDraft | None:
    async with _db_connect() as db:
        async with db.execute(
            "SELECT draft_json FROM remediation_drafts WHERE cluster_id = ?",
            (cluster_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                return RemediationDraft.model_validate_json(row[0])
            except Exception:
                return None


async def save_telemetry_signals(signals: list[TelemetrySignal]) -> tuple[int, int]:
    ingested = 0
    rejected = 0
    async with _db_connect() as db:
        for signal in signals:
            try:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO telemetry_signals
                    (signal_id, app_url, source, ts, signal_type, journey_key, route, value, unit, tags_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal.signal_id,
                        signal.app_url,
                        signal.source,
                        signal.ts,
                        signal.signal_type,
                        signal.journey_key,
                        signal.route,
                        signal.value,
                        signal.unit,
                        json.dumps(signal.tags),
                    ),
                )
                ingested += 1
            except Exception:
                rejected += 1
        await db.commit()
    return ingested, rejected


async def list_telemetry_signals(app_url: str, limit: int = 1000) -> list[dict]:
    safe_limit = max(1, min(limit, 5000))
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT signal_id, app_url, source, ts, signal_type, journey_key, route, value, unit, tags_json
            FROM telemetry_signals
            WHERE app_url = ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (app_url, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            out: list[dict] = []
            for row in rows:
                try:
                    tags = json.loads(row[9]) if row[9] else {}
                except Exception:
                    tags = {}
                out.append(
                    {
                        "signal_id": row[0],
                        "app_url": row[1],
                        "source": row[2],
                        "ts": row[3],
                        "signal_type": row[4],
                        "journey_key": row[5],
                        "route": row[6],
                        "value": row[7],
                        "unit": row[8],
                        "tags": tags,
                    }
                )
            return out


async def save_correlation_report(app_url: str, window: str, report: dict) -> str:
    report_id = str(uuid.uuid4())
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT INTO correlation_reports (report_id, app_url, window, report_json)
            VALUES (?, ?, ?, ?)
            """,
            (report_id, app_url, window, json.dumps(report)),
        )
        await db.commit()
    return report_id


async def save_risk_calibration_record(
    run_id: str,
    app_url: str,
    predicted_decision: str,
    blocker_count: int,
    critical_journey_failures: int,
    flow_ids: list[str],
) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO risk_calibration
            (record_id, run_id, app_url, predicted_decision, blocker_count, critical_journey_failures, flow_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                run_id,
                app_url,
                predicted_decision,
                blocker_count,
                critical_journey_failures,
                json.dumps(flow_ids),
            ),
        )
        await db.commit()


async def list_risk_calibration(app_url: str, limit: int = 100) -> list[dict]:
    """Return recent risk calibration records for an app, newest first."""
    safe_limit = max(1, min(limit, 500))
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT record_id, run_id, app_url, predicted_decision,
                   blocker_count, critical_journey_failures, created_at
            FROM risk_calibration
            WHERE app_url = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (app_url, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "record_id": r[0],
                    "run_id": r[1],
                    "app_url": r[2],
                    "predicted_decision": r[3],
                    "blocker_count": r[4],
                    "critical_journey_failures": r[5],
                    "created_at": r[6],
                }
                for r in rows
            ]


async def _get_schema_version(db) -> int:
    try:
        async with db.execute("SELECT version FROM schema_version LIMIT 1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


async def _set_schema_version(db, version: int) -> None:
    await db.execute("DELETE FROM schema_version")
    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


async def list_cases_for_flow_since(flow_id: str, since_iso: str, limit: int = 500) -> list[FailureCase]:
    """Return cases for a flow where the parent run started at or after since_iso."""
    safe_limit = max(1, min(limit, 500))
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT rc.result_json FROM run_cases rc
            JOIN runs r ON rc.run_id = r.run_id
            WHERE rc.flow_id = ? AND r.started_at >= ?
            ORDER BY r.started_at DESC
            LIMIT ?
            """,
            (flow_id, since_iso, safe_limit),
        ) as cursor:
            rows = await cursor.fetchall()
            cases: list[FailureCase] = []
            for row in rows:
                try:
                    cases.append(FailureCase.model_validate_json(row[0]))
                except Exception:
                    _log.debug("failed to parse result_json for flow case (since)", exc_info=True)
            return cases


async def save_release_brief(
    release_id: str,
    run_id: str,
    app_url: str,
    brief: dict,
) -> None:
    """Persist a ReleaseBrief as brief_json; keeps existing snapshot_json and project_id when set."""
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT snapshot_json, brief_json, run_id, project_id, created_at
            FROM release_snapshots WHERE release_id = ?
            """,
            (release_id,),
        ) as cursor:
            row = await cursor.fetchone()
        snapshot_json = json.dumps({})
        project_id: str | None = None
        created_at_keep: str | None = None
        if row:
            prev_snap, _prev_brief, _prev_run, prev_proj, prev_created = row
            created_at_keep = prev_created
            if prev_proj:
                project_id = prev_proj
            if prev_snap:
                try:
                    parsed = json.loads(prev_snap)
                    if isinstance(parsed, dict) and parsed:
                        snapshot_json = prev_snap
                    elif prev_snap.strip() not in ("", "{}"):
                        snapshot_json = prev_snap
                except Exception:
                    snapshot_json = prev_snap if prev_snap else json.dumps({})

        await db.execute(
            """
            INSERT OR REPLACE INTO release_snapshots
            (release_id, app_url, created_at, snapshot_json, brief_json, run_id, project_id)
            VALUES (?, ?, COALESCE(?, datetime('now')), ?, ?, ?, ?)
            """,
            (
                release_id,
                app_url,
                created_at_keep,
                snapshot_json,
                json.dumps(brief),
                run_id,
                project_id,
            ),
        )
        await db.commit()


async def get_release_brief(release_id: str) -> dict | None:
    """Retrieve a ReleaseBrief by release_id."""
    async with _db_connect() as db:
        async with db.execute(
            "SELECT brief_json, run_id, app_url FROM release_snapshots WHERE release_id = ?",
            (release_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row or not row[0]:
                return None
            try:
                brief = json.loads(row[0])
                # Ensure run_id and app_url are always present
                if isinstance(brief, dict):
                    brief.setdefault("run_id", row[1])
                    brief.setdefault("app_url", row[2])
                return brief
            except Exception:
                return None


async def get_release_row(release_id: str) -> dict | None:
    """Full release_snapshots row for HTTP API (registration + brief pointers)."""
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT release_id, app_url, created_at, snapshot_json, brief_json, run_id, project_id
            FROM release_snapshots WHERE release_id = ?
            """,
            (release_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "release_id": row[0],
                "app_url": row[1],
                "created_at": row[2],
                "snapshot_json": row[3],
                "brief_json": row[4],
                "run_id": row[5],
                "project_id": row[6],
            }


async def get_release_id_for_run(run_id: str) -> dict | None:
    """Map a run back to API release context (release_snapshots.run_id)."""
    if not run_id:
        return None
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT release_id, app_url FROM release_snapshots
            WHERE run_id = ? LIMIT 1
            """,
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {"release_id": row[0], "app_url": row[1]}


async def upsert_release_registration(
    *,
    release_id: str,
    app_url: str,
    project_id: str | None,
    registration_metadata: dict,
) -> None:
    """Create or update API release registration without clobbering brief_json/run_id."""
    now = datetime.now(timezone.utc).isoformat()
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT snapshot_json, brief_json, run_id, project_id, created_at
            FROM release_snapshots WHERE release_id = ?
            """,
            (release_id,),
        ) as cursor:
            row = await cursor.fetchone()

        snapshot_json: str
        brief_j: str | None = None
        run_j: str | None = None
        proj: str | None = project_id
        created_keep: str | None = None

        if row:
            prev_snap, brief_j, run_j, prev_proj, created_keep = row
            if proj is None and prev_proj:
                proj = prev_proj
            if prev_snap:
                try:
                    old = ReleaseSnapshot.model_validate_json(prev_snap)
                    merged_meta = {**(old.metadata or {}), **registration_metadata}
                    snap = old.model_copy(
                        update={"app_url": app_url, "metadata": merged_meta, "release_id": release_id}
                    )
                    snapshot_json = snap.model_dump_json()
                except Exception:
                    snap = ReleaseSnapshot(
                        release_id=release_id,
                        app_url=app_url,
                        created_at=now,
                        metadata=registration_metadata,
                    )
                    snapshot_json = snap.model_dump_json()
            else:
                snap = ReleaseSnapshot(
                    release_id=release_id,
                    app_url=app_url,
                    created_at=created_keep or now,
                    metadata=registration_metadata,
                )
                snapshot_json = snap.model_dump_json()
        else:
            snap = ReleaseSnapshot(
                release_id=release_id,
                app_url=app_url,
                created_at=now,
                metadata=registration_metadata,
            )
            snapshot_json = snap.model_dump_json()

        await db.execute(
            """
            INSERT OR REPLACE INTO release_snapshots
            (release_id, app_url, created_at, snapshot_json, brief_json, run_id, project_id)
            VALUES (?, ?, COALESCE(?, datetime('now')), ?, ?, ?, ?)
            """,
            (release_id, app_url, created_keep, snapshot_json, brief_j, run_j, proj),
        )
        await db.commit()


async def save_project(
    project_id: str,
    name: str,
    repo_url: str | None = None,
    metadata: dict | None = None,
) -> None:
    async with _db_connect() as db:
        await db.execute(
            """
            INSERT INTO projects (project_id, name, repo_url, metadata_json, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(project_id) DO UPDATE SET
                name = excluded.name,
                repo_url = excluded.repo_url,
                metadata_json = excluded.metadata_json
            """,
            (project_id, name, repo_url, json.dumps(metadata or {})),
        )
        await db.commit()


async def get_project(project_id: str) -> dict | None:
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT project_id, name, repo_url, metadata_json, created_at
            FROM projects WHERE project_id = ?
            """,
            (project_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                meta = json.loads(row[3]) if row[3] else {}
            except Exception:
                meta = {}
            return {
                "project_id": row[0],
                "name": row[1],
                "repo_url": row[2],
                "metadata": meta,
                "created_at": row[4],
            }


async def archive_old_runs(older_than_days: int = 30, keep_failed: bool = True) -> dict:
    """Delete runs (and associated cases/artifacts/events) older than older_than_days.

    If keep_failed is True, failed runs are retained regardless of age.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with _db_connect() as db:
        if keep_failed:
            async with db.execute(
                "SELECT run_id FROM runs WHERE started_at < ? AND status != 'failed'",
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT run_id FROM runs WHERE started_at < ?",
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()

        run_ids = [r[0] for r in rows]
        await db.execute("BEGIN IMMEDIATE")
        try:
            for run_id in run_ids:
                await db.execute("DELETE FROM run_cases WHERE run_id = ?", (run_id,))
                await db.execute("DELETE FROM artifacts WHERE run_id = ?", (run_id,))
                await db.execute("DELETE FROM run_health_events WHERE run_id = ?", (run_id,))
                await db.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "archived_runs": len(run_ids),
        "cutoff": cutoff,
        "kept_failed": keep_failed,
    }


async def archive_old_telemetry(older_than_days: int = 90) -> dict:
    """Delete telemetry signals older than older_than_days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with _db_connect() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM telemetry_signals WHERE ts < ?",
            (cutoff,),
        ) as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0
        await db.execute("DELETE FROM telemetry_signals WHERE ts < ?", (cutoff,))
        await db.commit()

    return {"archived_signals": count, "cutoff": cutoff}


async def get_latest_correlation_report(app_url: str, window: str) -> dict | None:
    async with _db_connect() as db:
        async with db.execute(
            """
            SELECT report_id, app_url, window, created_at, report_json
            FROM correlation_reports
            WHERE app_url = ? AND window = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (app_url, window),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                report = json.loads(row[4])
            except Exception:
                report = {}
            return {
                "report_id": row[0],
                "app_url": row[1],
                "window": row[2],
                "created_at": row[3],
                "report": report,
            }


# ── Release Policy CRUD (BLO-74) ──────────────────────────────────────────────


async def save_policy(policy: ReleasePolicy) -> None:
    """Persist a ReleasePolicy. If is_default=True, clears the flag on all others first."""
    async with _db_connect() as db:
        if policy.is_default:
            await db.execute("UPDATE release_policies SET is_default = 0")
        await db.execute(
            """
            INSERT INTO release_policies (policy_id, policy_name, policy_json, is_default)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                policy_name = excluded.policy_name,
                policy_json = excluded.policy_json,
                is_default  = excluded.is_default
            """,
            (
                policy.policy_id,
                policy.policy_name,
                policy.model_dump_json(),
                int(policy.is_default),
            ),
        )
        await db.commit()


async def get_policy(policy_id: str) -> ReleasePolicy | None:
    """Retrieve a ReleasePolicy by ID. Returns None if not found."""
    async with _db_connect() as db:
        async with db.execute(
            "SELECT policy_json FROM release_policies WHERE policy_id = ?",
            (policy_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                return ReleasePolicy.model_validate_json(row[0])
            except Exception:
                return None


async def get_default_policy() -> ReleasePolicy | None:
    """Return the policy marked is_default=1, or None if none is saved."""
    async with _db_connect() as db:
        async with db.execute("SELECT policy_json FROM release_policies WHERE is_default = 1 LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                return ReleasePolicy.model_validate_json(row[0])
            except Exception:
                return None


async def list_policies() -> list[ReleasePolicy]:
    """Return all stored ReleasePolicy objects, default first."""
    async with _db_connect() as db:
        async with db.execute(
            "SELECT policy_json FROM release_policies ORDER BY is_default DESC, created_at ASC"
        ) as cursor:
            rows = await cursor.fetchall()
    policies: list[ReleasePolicy] = []
    for row in rows:
        try:
            policies.append(ReleasePolicy.model_validate_json(row[0]))
        except Exception:
            _log.debug("Failed to parse policy row, skipping", exc_info=True)
    return policies
