import ipaddress
import logging
import os
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

# Suppress all logging before any imports
logging.disable(logging.CRITICAL)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "CRITICAL")

# Load .env from the repo root (two levels up from this file: src/blop/config.py → repo root)
try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)  # override=False: explicit env vars take precedence
except Exception:
    pass

GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")
LOGIN_URL: str = os.getenv("LOGIN_URL", "")
BLOP_ENV: str = os.getenv("BLOP_ENV", "development").strip().lower()

TEST_USERNAME: str = os.getenv("TEST_USERNAME", "")
TEST_PASSWORD: str = os.getenv("TEST_PASSWORD", "")
STORAGE_STATE_PATH: str = os.getenv("STORAGE_STATE_PATH", "")
COOKIE_JSON_PATH: str = os.getenv("COOKIE_JSON_PATH", "")


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse common boolean env-var forms."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ExplorationTuning(TypedDict):
    network_idle_wait_secs: float
    spa_settle_ms: int
    agent_max_failures: int
    agent_max_actions_per_step: int
    discover_max_pages: int


class CapturePolicy(TypedDict):
    trace: bool
    video: bool
    periodic_screenshots: bool
    navigation_screenshots: bool
    step_screenshots: bool
    failure_screenshots: bool
    final_screenshot: bool
    screenshot_interval_secs: float
    max_screenshots: int
    artifact_cap: int


_EXPLORATION_PRESETS: dict[str, ExplorationTuning] = {
    "default": {
        "network_idle_wait_secs": 2.0,
        "spa_settle_ms": 1500,
        "agent_max_failures": 5,
        "agent_max_actions_per_step": 6,
        "discover_max_pages": 10,
    },
    "saas_marketing": {
        # Better for tool-heavy SPAs with async rendering and cross-route transitions.
        "network_idle_wait_secs": 3.5,
        "spa_settle_ms": 2200,
        "agent_max_failures": 8,
        "agent_max_actions_per_step": 8,
        "discover_max_pages": 20,
    },
}

_CAPTURE_PRESETS: dict[str, CapturePolicy] = {
    "minimal": {
        "trace": False,
        "video": False,
        "periodic_screenshots": False,
        "navigation_screenshots": False,
        "step_screenshots": False,
        "failure_screenshots": True,
        "final_screenshot": True,
        "screenshot_interval_secs": 5.0,
        "max_screenshots": 4,
        "artifact_cap": 10,
    },
    "balanced": {
        "trace": False,
        "video": False,
        "periodic_screenshots": False,
        "navigation_screenshots": True,
        "step_screenshots": False,
        "failure_screenshots": True,
        "final_screenshot": True,
        "screenshot_interval_secs": 4.0,
        "max_screenshots": 12,
        "artifact_cap": 24,
    },
    "forensic": {
        "trace": True,
        "video": True,
        "periodic_screenshots": True,
        "navigation_screenshots": True,
        "step_screenshots": True,
        "failure_screenshots": True,
        "final_screenshot": True,
        "screenshot_interval_secs": 3.0,
        "max_screenshots": 60,
        "artifact_cap": 80,
    },
}


def get_exploration_tuning() -> ExplorationTuning:
    profile_name = os.getenv("BLOP_EXPLORATION_PROFILE", "default").strip().lower()
    preset = _EXPLORATION_PRESETS.get(profile_name, _EXPLORATION_PRESETS["default"]).copy()
    preset["network_idle_wait_secs"] = float(os.getenv("BLOP_NETWORK_IDLE_WAIT", str(preset["network_idle_wait_secs"])))
    preset["spa_settle_ms"] = int(os.getenv("BLOP_SPA_SETTLE_MS", str(preset["spa_settle_ms"])))
    preset["agent_max_failures"] = int(os.getenv("BLOP_AGENT_MAX_FAILURES", str(preset["agent_max_failures"])))
    preset["agent_max_actions_per_step"] = int(
        os.getenv("BLOP_AGENT_MAX_ACTIONS_PER_STEP", str(preset["agent_max_actions_per_step"]))
    )
    preset["discover_max_pages"] = int(os.getenv("BLOP_DISCOVERY_MAX_PAGES", str(preset["discover_max_pages"])))
    return preset


def get_capture_policy() -> CapturePolicy:
    profile_name = os.getenv("BLOP_CAPTURE_PROFILE", "balanced").strip().lower()
    preset = _CAPTURE_PRESETS.get(profile_name, _CAPTURE_PRESETS["balanced"]).copy()
    # A11y-first evidence: fewer automatic screenshots unless env explicitly sets capture flags.
    if _env_bool("BLOP_A11Y_FIRST_EVIDENCE", False):
        if os.getenv("BLOP_CAPTURE_NAV_SCREENSHOTS") is None:
            preset["navigation_screenshots"] = False
        if os.getenv("BLOP_CAPTURE_STEP_SCREENSHOTS") is None:
            preset["step_screenshots"] = False
        if os.getenv("BLOP_CAPTURE_PERIODIC_SCREENSHOTS") is None:
            preset["periodic_screenshots"] = False
    preset["trace"] = _env_bool("BLOP_CAPTURE_TRACE", preset["trace"])
    preset["video"] = _env_bool("BLOP_CAPTURE_VIDEO", preset["video"])
    preset["periodic_screenshots"] = _env_bool(
        "BLOP_CAPTURE_PERIODIC_SCREENSHOTS",
        preset["periodic_screenshots"],
    )
    preset["navigation_screenshots"] = _env_bool(
        "BLOP_CAPTURE_NAV_SCREENSHOTS",
        preset["navigation_screenshots"],
    )
    preset["step_screenshots"] = _env_bool(
        "BLOP_CAPTURE_STEP_SCREENSHOTS",
        preset["step_screenshots"],
    )
    preset["failure_screenshots"] = _env_bool(
        "BLOP_CAPTURE_FAILURE_SCREENSHOTS",
        preset["failure_screenshots"],
    )
    preset["final_screenshot"] = _env_bool(
        "BLOP_CAPTURE_FINAL_SCREENSHOT",
        preset["final_screenshot"],
    )
    preset["screenshot_interval_secs"] = float(
        os.getenv("BLOP_SCREENSHOT_INTERVAL_SECS", str(preset["screenshot_interval_secs"]))
    )
    preset["max_screenshots"] = int(os.getenv("BLOP_MAX_SCREENSHOTS", str(preset["max_screenshots"])))
    preset["artifact_cap"] = int(os.getenv("BLOP_MAX_EVIDENCE_ARTIFACTS", str(preset["artifact_cap"])))
    return preset


def get_durability_mode() -> str:
    raw = os.getenv("BLOP_DURABILITY_MODE", "exit").strip().lower()
    if raw in {"exit", "async", "sync"}:
        return raw
    return "exit"


# Resolve DB path: if relative, anchor to repo root so the server works from any CWD
_RAW_BLOP_DB_PATH = os.getenv("BLOP_DB_PATH", ".blop/runs.db")
if not os.path.isabs(_RAW_BLOP_DB_PATH):
    _REPO_ROOT = Path(__file__).parent.parent.parent
    BLOP_DB_PATH: str = str(_REPO_ROOT / _RAW_BLOP_DB_PATH)
else:
    BLOP_DB_PATH: str = _RAW_BLOP_DB_PATH
BLOP_HEADLESS: bool = os.getenv("BLOP_HEADLESS", "true").lower() == "true"
BLOP_MAX_STEPS: int = int(os.getenv("BLOP_MAX_STEPS", "50"))
BLOP_DISCOVERY_CONCURRENCY: int = int(os.getenv("BLOP_DISCOVERY_CONCURRENCY", "0"))
BLOP_REPLAY_CONCURRENCY: int = int(os.getenv("BLOP_REPLAY_CONCURRENCY", "0"))
# Fire-and-forget regression / release-check runs
BLOP_MAX_CONCURRENT_RUNS: int = max(1, int(os.getenv("BLOP_MAX_CONCURRENT_RUNS", "3")))
# LLM circuit breaker + bounded retries (used by ainvoke_llm)
BLOP_LLM_CIRCUIT_FAILURE_THRESHOLD: int = max(1, int(os.getenv("BLOP_LLM_CIRCUIT_FAILURE_THRESHOLD", "3")))
BLOP_LLM_CIRCUIT_COOLDOWN_SEC: float = float(os.getenv("BLOP_LLM_CIRCUIT_COOLDOWN_SEC", "60"))
BLOP_LLM_RETRY_MAX: int = max(0, min(8, int(os.getenv("BLOP_LLM_RETRY_MAX", "4"))))
# blop-http default rate limits (0 = fall back to server defaults)
BLOP_HTTP_RATE_LIMIT_PER_MIN: int = max(0, int(os.getenv("BLOP_HTTP_RATE_LIMIT_PER_MIN", "60")))
BLOP_HTTP_LLM_ROUTE_RATE_LIMIT_PER_MIN: int = max(0, int(os.getenv("BLOP_HTTP_LLM_ROUTE_RATE_LIMIT_PER_MIN", "10")))
BLOP_CAPTURE_PROFILE: str = os.getenv("BLOP_CAPTURE_PROFILE", "balanced").strip().lower()
BLOP_DURABILITY_MODE: str = get_durability_mode()

# Multi-LLM provider support
BLOP_LLM_PROVIDER: str = os.getenv("BLOP_LLM_PROVIDER", "google")
BLOP_LLM_MODEL: str = os.getenv("BLOP_LLM_MODEL", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# Extended thinking budget (0 = disabled)
BLOP_THINKING_BUDGET: int = int(os.getenv("BLOP_THINKING_BUDGET", "0"))

# Mobile / Appium
BLOP_APPIUM_URL: str = os.getenv("BLOP_APPIUM_URL", "http://127.0.0.1:4723")
# local | browserstack | lambdatest — cloud modes use hub URLs + vendor caps in driver.py
BLOP_MOBILE_PROVIDER: str = os.getenv("BLOP_MOBILE_PROVIDER", "local").strip().lower()
BLOP_BS_USER: str = os.getenv("BLOP_BS_USER", "")
BLOP_BS_KEY: str = os.getenv("BLOP_BS_KEY", "")
BLOP_LT_USER: str = os.getenv("BLOP_LT_USER", "")
BLOP_LT_KEY: str = os.getenv("BLOP_LT_KEY", "")

# Storage archival thresholds (days)
BLOP_ARCHIVE_RUNS_AFTER_DAYS: int = int(os.getenv("BLOP_ARCHIVE_RUNS_AFTER_DAYS", "30"))
BLOP_ARCHIVE_TELEMETRY_AFTER_DAYS: int = int(os.getenv("BLOP_ARCHIVE_TELEMETRY_AFTER_DAYS", "90"))

# Runs directory (screenshots, traces, console logs)
BLOP_RUNS_DIR: str = os.getenv("BLOP_RUNS_DIR", "")
BLOP_DEBUG_LOG: str = os.getenv("BLOP_DEBUG_LOG", "")
BLOP_REQUIRE_ABSOLUTE_PATHS: bool = _env_bool(
    "BLOP_REQUIRE_ABSOLUTE_PATHS",
    BLOP_ENV == "production",
)
BLOP_ALLOW_INTERNAL_URLS: bool = _env_bool("BLOP_ALLOW_INTERNAL_URLS", False)
BLOP_ALLOWED_HOSTS: tuple[str, ...] = tuple(
    h.strip().lower().lstrip(".") for h in os.getenv("BLOP_ALLOWED_HOSTS", "").split(",") if h.strip()
)
BLOP_RUN_TIMEOUT_SECS: int = int(os.getenv("BLOP_RUN_TIMEOUT_SECS", "0"))
BLOP_STEP_TIMEOUT_SECS: int = int(os.getenv("BLOP_STEP_TIMEOUT_SECS", "45"))

# Playwright-MCP compatibility layer settings
BLOP_COMPAT_OUTPUT_DIR: str = os.getenv("BLOP_COMPAT_OUTPUT_DIR", ".playwright-mcp")
BLOP_COMPAT_HEADLESS: bool = _env_bool("BLOP_COMPAT_HEADLESS", True)
BLOP_COMPAT_TEST_ID_ATTRIBUTE: str = os.getenv("BLOP_COMPAT_TEST_ID_ATTRIBUTE", "data-testid")
BLOP_TEST_ID_ATTRIBUTE: str = (
    os.getenv("BLOP_TEST_ID_ATTRIBUTE") or BLOP_COMPAT_TEST_ID_ATTRIBUTE or "data-testid"
).strip() or "data-testid"
BLOP_COMPAT_SNAPSHOT_MODE: str = (
    os.getenv("BLOP_COMPAT_SNAPSHOT_MODE") or os.getenv("BLOP_SNAPSHOT_MODE") or "incremental"
).lower()

# Privacy guard for screenshot-to-LLM visual triage uploads.
# Keep this disabled unless your screenshots are safe to send externally.
BLOP_ALLOW_SCREENSHOT_LLM: bool = _env_bool("BLOP_ALLOW_SCREENSHOT_LLM", False)

# Auto-heal confidence thresholds for regression replay
BLOP_AUTO_HEAL_MIN_CONFIDENCE: float = float(os.getenv("BLOP_AUTO_HEAL_MIN_CONFIDENCE", "0.78"))
BLOP_AUTO_HEAL_MAX_BEHAVIOR_RISK: float = float(os.getenv("BLOP_AUTO_HEAL_MAX_BEHAVIOR_RISK", "0.25"))

# Prompt overrides directory
BLOP_PROMPTS_DIR: str = os.getenv("BLOP_PROMPTS_DIR", "")

# Legacy auth URL env var (fallback for LOGIN_URL)
TEST_AUTH_URL: str = os.getenv("TEST_AUTH_URL", "")
BLOP_VALIDATE_AUTH_CACHE: bool = _env_bool("BLOP_VALIDATE_AUTH_CACHE", False)

# Hosted blop platform sync (optional — fire-and-forget push after each run_release_check)
BLOP_HOSTED_URL: str | None = os.getenv("BLOP_HOSTED_URL") or None  # e.g. https://app.blop.dev
BLOP_API_TOKEN: str | None = os.getenv("BLOP_API_TOKEN") or None  # blop_sk_… workspace API token
BLOP_PROJECT_ID: str | None = os.getenv("BLOP_PROJECT_ID") or None  # workspace project UUID

# Runtime contract version sent with all sync payloads.
# Must match SYNC_RUNTIME_CONTRACT_ALLOWED_VERSIONS on the cloud platform.
BLOP_RUNTIME_CONTRACT_VERSION: str = os.getenv("BLOP_RUNTIME_CONTRACT_VERSION", "2026-03-29")

# blop-http REST /v1 API (optional — same process as SSE server)
BLOP_HTTP_API_KEY: str | None = os.getenv("BLOP_HTTP_API_KEY") or None
# Optional workspace gate for hosted /v1 (requires matching headers on every request when token is set).
BLOP_HTTP_WORKSPACE_ID: str = (os.getenv("BLOP_HTTP_WORKSPACE_ID") or "").strip()
BLOP_HTTP_WORKSPACE_TOKEN: str = (os.getenv("BLOP_HTTP_WORKSPACE_TOKEN") or "").strip()
# Optional public base URL for absolute links in JSON (e.g. https://blop.example.com). If unset, handlers use request.base_url.
BLOP_HTTP_PUBLIC_BASE_URL: str = os.getenv("BLOP_HTTP_PUBLIC_BASE_URL", "").rstrip("/")


def hosted_sync_config_snapshot() -> dict:
    """Describe optional hosted sync posture without forcing cloud usage."""
    configured_fields = [
        key
        for key, value in {
            "BLOP_HOSTED_URL": BLOP_HOSTED_URL,
            "BLOP_API_TOKEN": BLOP_API_TOKEN,
            "BLOP_PROJECT_ID": BLOP_PROJECT_ID,
        }.items()
        if value
    ]
    missing_fields = [
        key
        for key, value in {
            "BLOP_HOSTED_URL": BLOP_HOSTED_URL,
            "BLOP_API_TOKEN": BLOP_API_TOKEN,
            "BLOP_PROJECT_ID": BLOP_PROJECT_ID,
        }.items()
        if not value
    ]
    fully_configured = len(configured_fields) == 3
    partially_configured = bool(configured_fields) and not fully_configured
    return {
        "enabled": fully_configured,
        "partial": partially_configured,
        "configured_fields": configured_fields,
        "missing_fields": missing_fields,
        "hosted_url": BLOP_HOSTED_URL,
        "project_id": BLOP_PROJECT_ID,
        "token_present": bool(BLOP_API_TOKEN),
    }


def is_cloud_sync_configured() -> bool:
    """Return True only when all three cloud sync vars are set."""
    return bool(BLOP_HOSTED_URL and BLOP_API_TOKEN and BLOP_PROJECT_ID)


def cloud_sync_missing_vars() -> list[str]:
    """Return names of cloud sync vars that are unset."""
    missing = []
    if not BLOP_HOSTED_URL:
        missing.append("BLOP_HOSTED_URL")
    if not BLOP_API_TOKEN:
        missing.append("BLOP_API_TOKEN")
    if not BLOP_PROJECT_ID:
        missing.append("BLOP_PROJECT_ID")
    return missing


def check_llm_api_key() -> tuple[bool, str]:
    """Return (has_key, key_name) based on the configured LLM provider."""
    provider = BLOP_LLM_PROVIDER.lower()
    if provider == "anthropic":
        return (bool(ANTHROPIC_API_KEY), "ANTHROPIC_API_KEY")
    if provider == "openai":
        return (bool(OPENAI_API_KEY), "OPENAI_API_KEY")
    return (bool(GOOGLE_API_KEY), "GOOGLE_API_KEY")


def validate_app_url(url: str) -> str | None:
    """Return an error message if *url* is not a valid HTTP(S) URL, else None."""
    if not url or not url.strip():
        return "app_url is required"
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return f"app_url must use http or https scheme, got '{parsed.scheme or '(none)'}'"
    if not parsed.netloc:
        return "app_url must include a host (e.g. https://example.com)"
    if parsed.username or parsed.password:
        return "app_url must not include URL credentials"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "app_url must include a valid hostname"
    if BLOP_ALLOWED_HOSTS:
        host_ok = any(host == allowed or host.endswith(f".{allowed}") for allowed in BLOP_ALLOWED_HOSTS)
        if not host_ok:
            return (
                "app_url host is not in BLOP_ALLOWED_HOSTS allowlist. "
                f"Got '{host}', allowlist={list(BLOP_ALLOWED_HOSTS)}"
            )
    if not BLOP_ALLOW_INTERNAL_URLS:
        if host in {"localhost", "host.docker.internal"} or host.endswith(".localhost"):
            return (
                "app_url points to an internal host ('localhost'). "
                "Set BLOP_ALLOW_INTERNAL_URLS=true for local development."
            )
        if host.endswith(".local") or host.endswith(".internal"):
            return "app_url points to an internal domain. Set BLOP_ALLOW_INTERNAL_URLS=true if this is intentional."
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            ip = None
        if ip and (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return (
                "app_url points to an internal/private IP address. "
                "Set BLOP_ALLOW_INTERNAL_URLS=true for trusted local networks."
            )
    return None


def validate_mobile_replay_app_url(app_url: str) -> str | None:
    """Validate app_url for mobile-only regression (package / bundle ID or label), not HTTP(S)."""
    if not app_url or not str(app_url).strip():
        return "app_url is required (use the same package name or bundle ID as in record_test_flow)"
    s = app_url.strip()
    if len(s) > 512:
        return "app_url is too long"
    return None


def runtime_config_issues() -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for runtime configuration sanity checks."""
    errors: list[str] = []
    warnings: list[str] = []

    if BLOP_MAX_STEPS <= 0:
        errors.append("BLOP_MAX_STEPS must be > 0")
    if BLOP_RUN_TIMEOUT_SECS < 0:
        errors.append("BLOP_RUN_TIMEOUT_SECS must be >= 0")
    if BLOP_STEP_TIMEOUT_SECS <= 0:
        errors.append("BLOP_STEP_TIMEOUT_SECS must be > 0")
    if BLOP_DISCOVERY_CONCURRENCY < 0:
        errors.append("BLOP_DISCOVERY_CONCURRENCY must be >= 0")
    if BLOP_REPLAY_CONCURRENCY < 0:
        errors.append("BLOP_REPLAY_CONCURRENCY must be >= 0")
    if BLOP_MAX_SCREENSHOTS <= 0:
        errors.append("BLOP_MAX_SCREENSHOTS must be > 0")
    if BLOP_SCREENSHOT_INTERVAL_SECS <= 0:
        errors.append("BLOP_SCREENSHOT_INTERVAL_SECS must be > 0")
    if not 0.0 <= BLOP_AUTO_HEAL_MIN_CONFIDENCE <= 1.0:
        errors.append("BLOP_AUTO_HEAL_MIN_CONFIDENCE must be between 0.0 and 1.0")
    if not 0.0 <= BLOP_AUTO_HEAL_MAX_BEHAVIOR_RISK <= 1.0:
        errors.append("BLOP_AUTO_HEAL_MAX_BEHAVIOR_RISK must be between 0.0 and 1.0")
    if BLOP_REQUIRE_ABSOLUTE_PATHS:
        raw_runs_dir = os.getenv("BLOP_RUNS_DIR", "")
        if raw_runs_dir and not os.path.isabs(raw_runs_dir):
            errors.append("BLOP_RUNS_DIR must be an absolute path in production mode")
        raw_debug_log = os.getenv("BLOP_DEBUG_LOG", "")
        if raw_debug_log and not os.path.isabs(raw_debug_log):
            errors.append("BLOP_DEBUG_LOG must be an absolute path in production mode")
        raw_db_path = os.getenv("BLOP_DB_PATH", "")
        if raw_db_path and not os.path.isabs(raw_db_path):
            errors.append("BLOP_DB_PATH must be an absolute path in production mode")
    if BLOP_ENV == "production" and BLOP_ALLOW_INTERNAL_URLS:
        warnings.append("BLOP_ALLOW_INTERNAL_URLS=true in production increases SSRF risk")
    caps_profile = os.getenv("BLOP_CAPABILITIES_PROFILE", "").strip().lower()
    if caps_profile and caps_profile not in {"production_minimal", "production_debug", "full"}:
        warnings.append(
            "BLOP_CAPABILITIES_PROFILE is unknown; expected one of production_minimal, production_debug, full"
        )
    capture_profile = os.getenv("BLOP_CAPTURE_PROFILE", "balanced").strip().lower()
    if capture_profile and capture_profile not in _CAPTURE_PRESETS:
        warnings.append(f"BLOP_CAPTURE_PROFILE is unknown; expected one of {sorted(_CAPTURE_PRESETS)}")
    raw_durability_mode = os.getenv("BLOP_DURABILITY_MODE", "exit").strip().lower()
    if raw_durability_mode and raw_durability_mode not in {"exit", "async", "sync"}:
        warnings.append("BLOP_DURABILITY_MODE is unknown; expected one of exit, async, sync")
    hosted_sync = hosted_sync_config_snapshot()
    if hosted_sync["partial"]:
        warnings.append(
            "Hosted sync is partially configured. Set BLOP_HOSTED_URL, BLOP_API_TOKEN, "
            "and BLOP_PROJECT_ID together, or leave all three unset for local-only mode."
        )
    return errors, warnings


def runtime_posture_snapshot() -> dict:
    """Return a summary of the current runtime posture for health/doctor surfaces."""
    errors, warnings = runtime_config_issues()
    has_key, key_name = check_llm_api_key()
    caps_profile = os.getenv("BLOP_CAPABILITIES_PROFILE", "").strip().lower() or "unspecified"
    return {
        "environment": BLOP_ENV,
        "llm_provider": BLOP_LLM_PROVIDER.lower(),
        "llm_key_name": key_name,
        "llm_key_present": has_key,
        "capabilities_profile": caps_profile,
        "compat_tools_enabled": BLOP_ENABLE_COMPAT_TOOLS,
        "legacy_mcp_tools_enabled": BLOP_ENABLE_LEGACY_MCP_TOOLS,
        "require_absolute_paths": BLOP_REQUIRE_ABSOLUTE_PATHS,
        "allow_internal_urls": BLOP_ALLOW_INTERNAL_URLS,
        "allowed_hosts": list(BLOP_ALLOWED_HOSTS),
        "timeouts": {
            "run_timeout_secs": BLOP_RUN_TIMEOUT_SECS,
            "step_timeout_secs": BLOP_STEP_TIMEOUT_SECS,
        },
        "concurrency": {
            "discovery_workers": BLOP_DISCOVERY_CONCURRENCY,
            "replay_workers": BLOP_REPLAY_CONCURRENCY,
        },
        "capture": {
            "profile": BLOP_CAPTURE_PROFILE,
            "trace": BLOP_CAPTURE_TRACE,
            "video": BLOP_CAPTURE_VIDEO,
            "periodic_screenshots": BLOP_CAPTURE_PERIODIC_SCREENSHOTS,
            "navigation_screenshots": BLOP_CAPTURE_NAV_SCREENSHOTS,
            "step_screenshots": BLOP_CAPTURE_STEP_SCREENSHOTS,
            "max_screenshots": BLOP_MAX_SCREENSHOTS,
            "artifact_cap": BLOP_MAX_EVIDENCE_ARTIFACTS,
        },
        "durability_mode": BLOP_DURABILITY_MODE,
        "paths": {
            "db_path": BLOP_DB_PATH,
            "db_path_absolute": os.path.isabs(BLOP_DB_PATH),
            "runs_dir": BLOP_RUNS_DIR,
            "runs_dir_absolute": bool(BLOP_RUNS_DIR) and os.path.isabs(BLOP_RUNS_DIR),
            "debug_log": BLOP_DEBUG_LOG,
            "debug_log_absolute": bool(BLOP_DEBUG_LOG) and os.path.isabs(BLOP_DEBUG_LOG),
        },
        "hosted_sync": hosted_sync_config_snapshot(),
        "issues": {
            "errors": errors,
            "warnings": warnings,
        },
    }


# SPA / complex workflow tuning
_EXPLORATION_TUNING = get_exploration_tuning()
# How long to wait for network idle after page load (seconds).
BLOP_NETWORK_IDLE_WAIT: float = _EXPLORATION_TUNING["network_idle_wait_secs"]
# Default settle wait after SPA navigation (ms) — used by wait_for_spa_ready.
BLOP_SPA_SETTLE_MS: int = _EXPLORATION_TUNING["spa_settle_ms"]
BLOP_AGENT_MAX_FAILURES: int = _EXPLORATION_TUNING["agent_max_failures"]
BLOP_AGENT_MAX_ACTIONS_PER_STEP: int = _EXPLORATION_TUNING["agent_max_actions_per_step"]
BLOP_DISCOVERY_MAX_PAGES: int = _EXPLORATION_TUNING["discover_max_pages"]

# Release recommendation policy gates (Phase 3)
BLOP_BLOCK_ON_REVENUE_FAILURE: bool = _env_bool("BLOP_BLOCK_ON_REVENUE_FAILURE", False)
BLOP_BLOCK_ON_ACTIVATION_FAILURE: bool = _env_bool("BLOP_BLOCK_ON_ACTIVATION_FAILURE", False)
BLOP_BLOCK_ON_ANY_FAILURE: bool = _env_bool("BLOP_BLOCK_ON_ANY_FAILURE", False)

# Recommendation staleness threshold (hours after run completion)
BLOP_RECOMMENDATION_STALE_HOURS: int = int(os.getenv("BLOP_RECOMMENDATION_STALE_HOURS", "24"))

# v2 risk score thresholds (Phase 2)
BLOP_RISK_THRESHOLD_BLOCKER: float = float(os.getenv("BLOP_RISK_THRESHOLD_BLOCKER", "80"))
BLOP_RISK_THRESHOLD_HIGH: float = float(os.getenv("BLOP_RISK_THRESHOLD_HIGH", "60"))
BLOP_RISK_THRESHOLD_MEDIUM: float = float(os.getenv("BLOP_RISK_THRESHOLD_MEDIUM", "30"))

# Compat tool surface gate — when true, exposes Playwright-parity browser_* aliases, blop_v2_*,
# and other legacy/extra tools. Default false keeps the surface smaller; core release tools,
# context-read tools (get_workspace_context, …), and atomic browser tools stay registered.
BLOP_ENABLE_COMPAT_TOOLS: bool = _env_bool("BLOP_ENABLE_COMPAT_TOOLS", False)

# Deprecated MCP aliases (discover_test_flows, run_regression_test, validate_setup).
# Default false keeps the agent-facing surface smaller; set true only for backward compatibility.
BLOP_ENABLE_LEGACY_MCP_TOOLS: bool = _env_bool("BLOP_ENABLE_LEGACY_MCP_TOOLS", False)

# Auth/login wait tuning
BLOP_AUTH_LOGIN_POLL_STEPS: int = int(os.getenv("BLOP_AUTH_LOGIN_POLL_STEPS", "40"))
BLOP_AUTH_LOGIN_POLL_INTERVAL_MS: int = int(os.getenv("BLOP_AUTH_LOGIN_POLL_INTERVAL_MS", "500"))
BLOP_AUTH_NETWORKIDLE_TIMEOUT_MS: int = int(os.getenv("BLOP_AUTH_NETWORKIDLE_TIMEOUT_MS", "8000"))

# Recording/discovery wait tuning
BLOP_RECORDING_ENTRY_SETTLE_MS: int = int(os.getenv("BLOP_RECORDING_ENTRY_SETTLE_MS", "5000"))

# Interaction readiness caps
BLOP_INTERACTION_LOADING_CAP_MS: int = int(os.getenv("BLOP_INTERACTION_LOADING_CAP_MS", "8000"))
BLOP_INTERACTION_EDITOR_LOADING_CAP_MS: int = int(os.getenv("BLOP_INTERACTION_EDITOR_LOADING_CAP_MS", "20000"))

_CAPTURE_POLICY = get_capture_policy()
BLOP_CAPTURE_TRACE: bool = _CAPTURE_POLICY["trace"]
BLOP_CAPTURE_VIDEO: bool = _CAPTURE_POLICY["video"]
BLOP_CAPTURE_PERIODIC_SCREENSHOTS: bool = _CAPTURE_POLICY["periodic_screenshots"]
BLOP_CAPTURE_NAV_SCREENSHOTS: bool = _CAPTURE_POLICY["navigation_screenshots"]
BLOP_CAPTURE_STEP_SCREENSHOTS: bool = _CAPTURE_POLICY["step_screenshots"]
BLOP_CAPTURE_FAILURE_SCREENSHOTS: bool = _CAPTURE_POLICY["failure_screenshots"]
BLOP_CAPTURE_FINAL_SCREENSHOT: bool = _CAPTURE_POLICY["final_screenshot"]
BLOP_SCREENSHOT_INTERVAL_SECS: float = _CAPTURE_POLICY["screenshot_interval_secs"]
BLOP_MAX_SCREENSHOTS: int = _CAPTURE_POLICY["max_screenshots"]
BLOP_MAX_EVIDENCE_ARTIFACTS: int = _CAPTURE_POLICY["artifact_cap"]
