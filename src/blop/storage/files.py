from __future__ import annotations

import re
from pathlib import Path

# Anchor runs/ to the repo root so paths are stable regardless of CWD.
# files.py lives at src/blop/storage/files.py → 4 levels up = repo root
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_SAFE_REPORT_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ALLOWED_REPORT_FORMATS = {"md", "txt", "json", "html"}


def _runs_dir() -> Path:
    """Return the absolute path to the runs/ directory."""
    from blop.config import BLOP_RUNS_DIR

    if BLOP_RUNS_DIR:
        configured = Path(BLOP_RUNS_DIR)
        if configured.is_absolute():
            return configured
        return (_REPO_ROOT / configured).resolve()
    return _REPO_ROOT / "runs"


def _validate_report_token(value: str, *, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    if "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"Invalid {field_name}: path separators and traversal are not allowed")
    return value


def _validate_report_run_id(run_id: str) -> str:
    safe_run_id = _validate_report_token(run_id, field_name="run_id")
    if not _SAFE_REPORT_RUN_ID_RE.fullmatch(safe_run_id):
        raise ValueError("Invalid run_id: only letters, numbers, '_', '-', and '.' are allowed")
    return safe_run_id


def _validate_component(value: str, *, field_name: str) -> str:
    token = _validate_report_token(value, field_name=field_name)
    if not _SAFE_COMPONENT_RE.fullmatch(token):
        raise ValueError(f"Invalid {field_name}: only letters, numbers, '_', '-', and '.' are allowed")
    return token


def _validate_report_format(fmt: str) -> str:
    safe_fmt = _validate_report_token(fmt.lower(), field_name="fmt")
    if safe_fmt not in _ALLOWED_REPORT_FORMATS:
        raise ValueError(f"Invalid fmt: must be one of {sorted(_ALLOWED_REPORT_FORMATS)}")
    return safe_fmt


def ensure_run_dirs(run_id: str) -> str:
    """Create per-run subdirectories under runs/ and return the base run dir."""
    run_id = _validate_component(run_id, field_name="run_id")
    base = _runs_dir()
    for sub in ("screenshots", "traces", "console", "network"):
        (base / sub / run_id).mkdir(parents=True, exist_ok=True)
    return str(base / run_id)


def screenshot_path(run_id: str, case_id: str, step: int) -> str:
    run_id = _validate_component(run_id, field_name="run_id")
    case_id = _validate_component(case_id, field_name="case_id")
    dir_ = _runs_dir() / "screenshots" / run_id / case_id
    dir_.mkdir(parents=True, exist_ok=True)
    return str(dir_ / f"step_{step:03d}.png")


def trace_path(run_id: str, case_id: str) -> str:
    run_id = _validate_component(run_id, field_name="run_id")
    case_id = _validate_component(case_id, field_name="case_id")
    dir_ = _runs_dir() / "traces" / run_id
    dir_.mkdir(parents=True, exist_ok=True)
    return str(dir_ / f"{case_id}.zip")


def console_log_path(run_id: str, case_id: str) -> str:
    run_id = _validate_component(run_id, field_name="run_id")
    case_id = _validate_component(case_id, field_name="case_id")
    dir_ = _runs_dir() / "console" / run_id
    dir_.mkdir(parents=True, exist_ok=True)
    return str(dir_ / f"{case_id}.log")


def artifacts_dir(run_id: str) -> str:
    run_id = _validate_component(run_id, field_name="run_id")
    return str((_runs_dir() / run_id).resolve())


def baseline_dir(flow_id: str) -> Path:
    """Return the directory for golden baseline screenshots for a flow."""
    flow_id = _validate_component(flow_id, field_name="flow_id")
    d = _runs_dir() / "baselines" / flow_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def baseline_path(flow_id: str, step_index: int) -> str:
    return str(baseline_dir(flow_id) / f"step_{step_index:03d}.png")


def report_path(run_id: str, fmt: str = "md") -> str:
    safe_run_id = _validate_report_run_id(run_id)
    safe_fmt = _validate_report_format(fmt)
    d = _runs_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{safe_run_id}.{safe_fmt}")


def network_log_path(run_id: str, case_id: str) -> str:
    run_id = _validate_component(run_id, field_name="run_id")
    case_id = _validate_component(case_id, field_name="case_id")
    dir_ = _runs_dir() / "network" / run_id
    dir_.mkdir(parents=True, exist_ok=True)
    return str(dir_ / f"{case_id}.jsonl")


def mobile_screenshot_path(run_id: str, case_id: str, step: int, platform: str = "ios") -> str:
    run_id = _validate_component(run_id, field_name="run_id")
    case_id = _validate_component(case_id, field_name="case_id")
    platform = _validate_component(platform, field_name="platform")
    dir_ = _runs_dir() / "mobile" / platform / "screenshots" / run_id / case_id
    dir_.mkdir(parents=True, exist_ok=True)
    return str(dir_ / f"step_{step:03d}.png")


def mobile_page_source_path(run_id: str, case_id: str, step: int, platform: str = "ios") -> str:
    """Accessibility / UI hierarchy dump (Appium page_source XML) per replay step."""
    run_id = _validate_component(run_id, field_name="run_id")
    case_id = _validate_component(case_id, field_name="case_id")
    platform = _validate_component(platform, field_name="platform")
    dir_ = _runs_dir() / "mobile" / platform / "page_source" / run_id / case_id
    dir_.mkdir(parents=True, exist_ok=True)
    return str(dir_ / f"step_{step:03d}.xml")


def device_log_path(run_id: str, case_id: str, platform: str = "ios") -> str:
    run_id = _validate_component(run_id, field_name="run_id")
    case_id = _validate_component(case_id, field_name="case_id")
    platform = _validate_component(platform, field_name="platform")
    dir_ = _runs_dir() / "mobile" / platform / "logs" / run_id
    dir_.mkdir(parents=True, exist_ok=True)
    suffix = "syslog" if platform == "ios" else "logcat"
    return str(dir_ / f"{case_id}.{suffix}")


def codegen_path(flow_id: str, ext: str = "py") -> str:
    flow_id = _validate_component(flow_id, field_name="flow_id")
    d = _runs_dir() / "codegen"
    d.mkdir(parents=True, exist_ok=True)
    allowed_exts = {"py", "json", "txt"}
    safe_ext = (ext or "py").strip().lower().lstrip(".")
    if "/" in safe_ext or "\\" in safe_ext or safe_ext not in allowed_exts:
        safe_ext = "py"
    return str(d / f"{flow_id}.{safe_ext}")
