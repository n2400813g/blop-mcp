# blop — The MCP-Native Release Confidence Control Plane

Most AI testing tools can click through pages. Teams still do not know whether they should ship.

**blop** turns browser execution into **release decisions** by combining business-critical journey context, evidence-heavy QA runs, and risk governance in one MCP-native control plane.

You do not write test code. You ask in chat, then ship with auditable evidence, prioritized risk, and a clear go/no-go recommendation.

Compatible MCP clients: **Cursor**, **Claude Code**, and other clients that support MCP tool/resource workflows.

## Quick Navigation

- [Product thesis](#product-thesis)
- [What it actually does](#what-does-it-actually-do)
- [Why blop vs generic AI testing agents](#why-blop-vs-generic-ai-testing-agents)
- [Before you start](#before-you-start--what-youll-need)
- [Install](#installation--step-by-step)
- [Release packaging](#release-packaging)
- [Configure and connect](#configure-and-connect-blop)
- [Production setup](#production-setup-local-managed-stdio)
- [5-minute quickstart](#your-first-mcp-native-run-in-5-minutes)
- [Auth session guidance](#auth-session-guidance)
- [Production Client Quickstarts](#production-client-quickstarts)
- [Business-context QA scenarios](#real-world-testing-scenarios-for-b2b-saas)
- [MCP resources + v2 surface](#mcp-resources--v2-surface-summary)
- [Troubleshooting](#troubleshooting)
- [Full tool reference](#full-tool-reference)

---

## Product thesis

- **Core belief:** teams do not have a "generate more tests" problem; they have a **release confidence** problem.
- **What competitors miss:** bug detection without business weighting creates noisy output and weak ship/no-ship decisions.
- **What blop uniquely does:** connect context graphs, regression evidence, incident patterns, and telemetry into a single risk narrative that leaders can act on.

---

## What does it actually do?

1. **Captures context** with inventory + graph resources agents can read cheaply
2. **Discovers and records** business-critical flows from that context
3. **Replays** flows asynchronously to catch regressions before release
4. **Correlates evidence** across screenshots, traces, run health, and telemetry signals
5. **Scores risk** so teams can prioritize or gate releases with confidence

It plugs into **Cursor** or **Claude Code** as an MCP tool — meaning you just ask it to run tests in a chat window, the same way you'd ask a colleague.

### Who this helps

- **QA + developers:** quickly discover, record, and replay business-critical flows with deterministic evidence.
- **Engineering managers:** tie regressions to business impact with release-risk scoring, clusters, and remediation guidance.

### Trust and operations at a glance

- **Read-only context:** resources (`blop://...`) are for low-token retrieval and planning.
- **Action tools:** tools execute browser actions, replays, recording, and risk analysis.
- **Artifact storage:** runs, screenshots, traces, and logs are persisted locally (`.blop/` and `runs/`).
- **Auth behavior:** auth sessions are cached and validated; expired sessions are surfaced before critical runs.

---

## Why blop vs generic AI testing agents?

Generic browser agents optimize for test execution throughput. blop optimizes for decision quality under uncertainty.

| Dimension | Generic AI browser runner | blop MCP-native approach |
|-----------|---------------------------|---------------------------|
| Output shape | Mostly conversational text | Structured contracts + typed envelopes via `blop_v2_get_surface_contract` and v2 resources |
| Context handling | Re-run flows to recover context | Read `blop://...` resources first (`inventory`, `context-graph`, `artifact-index`, `stability-profile`) |
| Ops model | One-shot execution focus | Async run lifecycle with health stream, run states, and artifact indexing |
| Release decisions | Manual interpretation | Risk scoring, incident clustering, remediation drafts, telemetry correlation |
| Client portability | Tooling-specific patterns | Standard MCP tool/resource model across Cursor, Claude Code, and compatible clients |

Proof points in implementation:
- Contract definitions and stable resource envelope in `src/blop/tools/v2_surface.py`
- Correlation/risk persistence in `src/blop/storage/sqlite.py`
- Structured run reporting in `src/blop/reporting/results.py`

If your release process needs a deterministic answer to "can we ship this safely?", blop is purpose-built for that question.

---

## Before you start — what you'll need

| What | Where to get it | Takes |
|------|----------------|-------|
| Python 3.11 or newer | [python.org/downloads](https://python.org/downloads) | 5 min |
| `uv` (fast Python installer) | Run `curl -LsSf https://astral.sh/uv/install.sh \| sh` in Terminal | 1 min |
| Google API key (free tier works) | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | 2 min |
| Cursor or Claude Code | [cursor.com](https://cursor.com) or `npm i -g @anthropic-ai/claude-code` | 5 min |
| Chromium runtime | Installed by `playwright install chromium --with-deps --no-shell` | 2-5 min |

---

## Installation — step by step

Open Terminal and run these commands one at a time:

```bash
# 1. Go into the blop folder
cd /path/to/blop-mcp

# 2. Create a Python environment
uv venv && source .venv/bin/activate

# 3. Install blop
uv pip install -e .

# 4. Install the browser blop controls
playwright install chromium --with-deps --no-shell
```

You should see no errors. If you do, check the [Troubleshooting](#troubleshooting) section below.

## Release packaging

`blop-mcp` now supports a standard Python distribution path for PyPI publishing while preserving the `blop` and `blop-mcp` CLI commands.

Local maintainer smoke check:

```bash
uv pip install -e ".[dev]"
python -m build
python -m venv /tmp/blop-dist-smoke
source /tmp/blop-dist-smoke/bin/activate
pip install dist/*.whl
blop --help
blop-mcp --help
```

This should build both the sdist and wheel, install the wheel into a clean environment, and verify the console entrypoints start.

Release instructions:

- Build and verify distributions locally using the smoke check above.
- Upload the verified artifacts to PyPI with `python -m twine upload dist/*`.
- Full maintainer notes: [`docs/releasing.md`](docs/releasing.md)

---

## Configure and connect blop

### 1) Configure credentials

Copy the example config file and fill it in:

```bash
cp .env.example .env
```

Then open `.env` in any text editor and fill in your values:

```
# Required — get this from aistudio.google.com/app/apikey
GOOGLE_API_KEY=your_key_here

# Your app's URL
APP_BASE_URL=https://your-app.com

# Login details (only needed for testing authenticated pages)
LOGIN_URL=https://your-app.com/login
TEST_USERNAME=your@email.com
TEST_PASSWORD=your_password
```

Everything else can stay as-is.

### 2) Connect to your IDE

### Cursor

1. Open Cursor
2. Go to **Settings → MCP**
3. Click **Add MCP Server** and paste this (update the path to where you cloned blop):

```json
{
  "mcpServers": {
    "blop": {
      "command": "uv",
      "args": ["--directory", "/path/to/blop-mcp", "run", "python", "-m", "blop.server"],
      "env": {
        "GOOGLE_API_KEY": "your_key_here",
        "APP_BASE_URL": "https://your-app.com",
        "LOGIN_URL": "https://your-app.com/login",
        "TEST_USERNAME": "your@email.com",
        "TEST_PASSWORD": "your_password"
      }
    }
  }
}
```

4. Restart Cursor. You should see **blop** listed as a connected tool in Settings → MCP.

### Claude Code

Run this once in your terminal:

```bash
claude mcp add blop /path/to/blop-mcp/.venv/bin/blop-mcp \
  -e GOOGLE_API_KEY="your_key_here" \
  -e APP_BASE_URL="https://your-app.com" \
  -e LOGIN_URL="https://your-app.com/login" \
  -e TEST_USERNAME="your@email.com" \
  -e TEST_PASSWORD="your_password"
```

Type `/mcp` in Claude Code to verify — you should see `blop: connected`.

---

## Production setup (local managed stdio)

blop production is optimized for a **client-managed `stdio` MCP process** (Cursor/Claude launching `blop-mcp`), with strict runtime/path validation and least-privilege tool exposure.

- Full guide: [`docs/production_setup.md`](docs/production_setup.md)
- Production env template: [`deploy/prod.env.template`](deploy/prod.env.template)
- Optional SSE sidecar systemd unit: [`deploy/systemd/blop-http.service`](deploy/systemd/blop-http.service)
- Optional container baseline: [`deploy/docker/Dockerfile`](deploy/docker/Dockerfile)

Recommended production posture:

- `BLOP_ENV=production`
- `BLOP_REQUIRE_ABSOLUTE_PATHS=true`
- absolute `BLOP_DB_PATH`, `BLOP_RUNS_DIR`, and `BLOP_DEBUG_LOG`
- `BLOP_ALLOW_INTERNAL_URLS=false` (default-safe URL policy)
- `BLOP_CAPABILITIES_PROFILE=production_minimal`
- `BLOP_ENABLE_COMPAT_TOOLS=false` unless explicitly required

---

## Your first MCP-native run in 5 minutes

Open a chat window in Cursor or Claude Code and paste this (swap in your app URL):

```
Use blop to test https://your-app.com

1. Call validate_release_setup(app_url="https://your-app.com") and stop if status is "blocked"
2. Call discover_critical_journeys(app_url="https://your-app.com") to find the release-gating journeys
3. Read blop://journeys and pick the gated journeys that matter for this release
4. Record or refresh those journeys with record_test_flow(...) so release checks run against real saved flows
5. Call run_release_check(app_url="https://your-app.com", journey_ids=[...], mode="replay")
6. Poll get_test_results(run_id="...") until status is terminal
7. Read blop://release/{release_id}/brief and blop://release/{release_id}/artifacts
8. If the decision is not SHIP, call triage_release_blocker(run_id="...") and summarize blockers, evidence, and next actions
```

That is the canonical MVP loop: validate, discover, record, replay, and triage.

## Control-plane workflow (business context + QA)

1. **Preflight:** confirm readiness with `validate_release_setup`.
2. **Discover:** identify release-gating paths with `discover_critical_journeys`.
3. **Record:** capture or refresh the gated journeys with `record_test_flow`.
4. **Execute:** run `run_release_check` in `replay` mode against recorded flows.
5. **Triage:** use `triage_release_blocker` plus `blop://release/*` resources to turn failures into decisions.

`targeted` mode is still available for one-off exploratory checks, but it is a shortcut, not the golden path for release gating. For larger public sites, you can raise its one-shot budget with `BLOP_TARGETED_MAX_STEPS` (default `40`).

## Auth session guidance

For protected apps, the most reliable path is:

1. Capture auth with `capture_auth_session(...)`
2. Run `validate_release_setup(app_url="https://your-app.com", profile_name="your_profile")`
3. Only then launch `run_release_check(..., mode="replay")`

If a run returns `waiting_auth` or validation says the session is expired:

- Re-run `capture_auth_session(...)` to refresh the saved session.
- Re-run `validate_release_setup(...)` to confirm the session lands inside the app.
- Retry the replay only after validation is clean.

If failure output points to stale recordings, refresh the affected journey with `record_test_flow(...)` before trusting replay failures as real regressions.

## Production Client Quickstarts

Canonical MCP-client setup guides:

- Cursor: [`docs/quickstart_cursor.md`](docs/quickstart_cursor.md)
- Claude Code: [`docs/quickstart_claude_code.md`](docs/quickstart_claude_code.md)
- Codex-compatible clients: [`docs/quickstart_codex.md`](docs/quickstart_codex.md)

Operational references:

- Production baseline: [`docs/production_setup.md`](docs/production_setup.md)
- Operator failure guide: [`docs/operator_failures.md`](docs/operator_failures.md)
- Stability measurement lives in existing outputs: `get_test_results(...)` includes per-run bucket summaries and `get_risk_analytics(...)` aggregates top buckets, blocker buckets, and highest-pain instability classes.
- Compatibility and deprecation policy: [`docs/compatibility_policy.md`](docs/compatibility_policy.md)
- Release notes template: [`docs/release_notes_template.md`](docs/release_notes_template.md)

### Release Readiness Brief (recommended output)

For each candidate release, summarize:

- Decision: **ship** or **hold**
- Risk level and score (from run outcomes + criticality weighting)
- Top 3 risks with direct evidence paths
- Immediate mitigation actions and target owner(s)

---

## Playwright-MCP compatible mode

blop now includes an additive compatibility layer for prompt portability across MCP clients
that expect Playwright-style `browser_*` tools (Cursor, Claude, Codex, Copilot, Windsurf, etc.).

Enable it by adding `compat_browser` to capabilities:

```bash
export BLOP_CAPABILITIES=core,auth,debug,compat_browser
```

Optional compatibility env vars:

| Variable | Default | Purpose |
|----------|---------|---------|
| `BLOP_COMPAT_OUTPUT_DIR` | `.playwright-mcp` | Where compatibility artifacts (snapshots/screenshots/storage-state files) are written |
| `BLOP_COMPAT_HEADLESS` | `true` | Run compatibility browser session headless or headed |
| `BLOP_COMPAT_TEST_ID_ATTRIBUTE` | `data-testid` | Preferred test id attribute used when building element selectors |
| `BLOP_COMPAT_SNAPSHOT_MODE` | `incremental` | Snapshot mode hint for compatibility workflows |

Typical interop flow:

1. `browser_navigate(url=...)`
2. `browser_snapshot()` to obtain `ref` handles
3. `browser_click(ref=...)` / `browser_type(ref=..., text=...)`
4. `browser_tabs(action="list"|"new"|"select")`
5. `browser_console_messages(...)`, `browser_network_requests(...)`
6. `browser_close()`

Auth bridge behavior:

- If a `profile_name` is passed to `browser_navigate`, blop resolves that saved auth profile first.
- If no profile is provided, compatibility mode falls back to env-driven auth state resolution.
- This keeps compatibility tools aligned with existing blop auth workflows (`save_auth_profile`, `capture_auth_session`).
- `browser_*` cookie/state/route tools act on the shared compat session only; use `get_browser_cookies`, `set_browser_cookie`, `save_browser_state`, and `mock_network_route` when you want URL-scoped storage operations or regression-run mocks.

Tool confusion matrix (use this / not that):

| If you want to... | Use this | Not that | Why |
|-------------------|----------|----------|-----|
| Inspect cookies for a specific URL/profile | `get_browser_cookies(app_url, profile_name?)` | `browser_cookie_list()` | `get_browser_cookies` is URL-scoped and runs in an ephemeral context; `browser_cookie_list` reads the shared compat session. |
| Set a cookie for URL-scoped auth state | `set_browser_cookie(app_url, ...)` | `browser_cookie_set(...)` | `set_browser_cookie` persists URL/profile state for blop auth flows; `browser_cookie_set` mutates only the shared compat session. |
| Save URL/profile storage state to disk | `save_browser_state(app_url, ...)` | `browser_storage_state(...)` | `save_browser_state` captures URL-scoped state; `browser_storage_state` exports the current shared compat session. |
| Mock APIs during regression replay runs | `mock_network_route(...)` | `browser_route(...)` | `mock_network_route` applies during regression execution; `browser_route` only affects the shared compat browser session. |
| Capture one-off exploratory QA output | `evaluate_web_task(...)` | `record_test_flow(...)` | `evaluate_web_task` returns immediate report output for one-off checks (see [full reference](#evaluate-web-task)); `record_test_flow` creates reusable flow artifacts for regression. |
| Create reusable regression flow IDs | `record_test_flow(...)` | `evaluate_web_task(...)` | `record_test_flow` is the source of reusable `flow_id` values consumed by `run_regression_test`. |
| Inspect page interaction structure from crawling context | `get_page_structure(app_url, url?)` | `browser_snapshot(...)` | `get_page_structure` is crawl/discovery-oriented; `browser_snapshot` is for current shared compat session state. |
| Start/stop the long-lived compat browser interaction loop | `browser_navigate(...)`, `browser_snapshot(...)`, `browser_click(...)` | `discover_test_flows(...)` | `browser_*` tools are imperative session controls; `discover_test_flows` is planning/crawl output, not interactive control. |

---

## Real-world testing scenarios for B2B SaaS

### Scenario 1 — New app, zero knowledge

```
Use blop to discover and test https://new-saas-app.com from scratch.

1. Call discover_test_flows with business_goal="Find all revenue-critical flows including signup, checkout, and onboarding"
2. Record the top 5 suggested flows by calling `record_test_flow` for each one
3. Run regression on all of them
4. Show me anything with severity "high" or "blocker"
```

### Scenario 2 — Before a release

```
We're about to ship a new version. Use blop to run a pre-release check on https://staging.myapp.com:

1. list_recorded_tests — show what flows we have
2. run_regression_test on all flows against staging
3. get_test_results — compare pass/fail to last run
4. debug_test_case on anything that changed from pass to fail
```

### Scenario 3 — Test the full authenticated product

```
Test the authenticated product experience on https://app.myapp.com:

1. save_auth_profile("prod-user", "env_login", login_url="https://app.myapp.com/login")
2. record_test_flow for: dashboard load, core feature (e.g. "create new project"), settings page, and billing/upgrade flow
3. run_regression_test with profile_name="prod-user"
4. get_test_results — show me the full breakdown
```

### Scenario 4 — Investigate a specific bug report

```
A user reported the checkout button isn't working. Use blop to investigate:

1. record_test_flow("https://myapp.com", "checkout_bug", "Navigate to pricing, click the Pro plan CTA, and verify checkout loads")
2. run_regression_test on that flow
3. get_test_results — check step_failure_index and assertion_failures
4. debug_test_case on the failure to get screenshots and a plain-English explanation
```

### Scenario 5 — Auth-gated flows with SSO

**Option A — Interactive capture (recommended):** Use `capture_auth_session` so blop opens a browser and you log in once; it saves the session and creates the profile automatically.

```
capture_auth_session(
  profile_name="sso-session",
  login_url="https://your-app.com/login",
  success_url_pattern="/dashboard",
  timeout_secs=120
)
```

For providers (e.g. Google, LinkedIn) that block "headless" or fresh contexts, use a persistent profile:

```
capture_auth_session(
  profile_name="sso-session",
  login_url="https://your-app.com/login",
  success_url_pattern="/dashboard",
  user_data_dir=".blop/chrome_profile_myapp"
)
```

**Option B — Manual export:** Log in manually, export the session (e.g. Playwright or Chrome DevTools -> Application -> Storage), then:

```
save_auth_profile(
  profile_name="sso-session",
  auth_type="storage_state",
  storage_state_path="/path/to/my-session.json"
)
```

---

## MCP resources + v2 surface summary

Use resources for cheap context retrieval before action:

- `blop://inventory/{app}`
- `blop://context-graph/{app}`
- `blop://run/{run_id}/artifact-index`
- `blop://flow/{flow_id}/stability-profile`

Use v2 for release-level governance:

- contracts: `blop_v2_get_surface_contract`
- release risk: `blop_v2_assess_release_risk`
- journey health: `blop_v2_get_journey_health`
- incidents/remediation: `blop_v2_cluster_incidents`, `blop_v2_generate_remediation`
- telemetry correlation: `blop_v2_get_correlation_report`

Detailed resources and v2 references are provided below in the full reference sections.

---

## Full tool reference

Recommended order for reliable MCP workflows:

1. **Preflight and contract**
   - `validate_setup`
   - `blop_v2_get_surface_contract`
2. **Context before action**
   - `explore_site_inventory`, `get_page_structure`
   - read `blop://inventory/...`, `blop://context-graph/...`
3. **Execution**
   - `discover_test_flows`, `record_test_flow`, `run_regression_test`
4. **Observability and governance**
   - `get_run_health_stream`, `get_test_results`, `get_risk_analytics`
   - v2: release risk, journey health, incident clustering, remediation, correlation

Detailed tool behavior is below.

### 1. `discover_test_flows` — *"What should I test?"*

Crawls your app and asks AI to figure out what the important user journeys are. Returns a list of suggested flows with descriptions of what to verify.

**Basic usage:**
```
discover_test_flows("https://your-app.com")
```

**With more context (gets better results):**
```
discover_test_flows(
  app_url="https://your-app.com",
  business_goal="Find all revenue-critical flows like checkout and upgrade",
  max_depth=2
)
```

**Parameters:**
| Parameter | What it does | Example |
|-----------|-------------|---------|
| `app_url` | The website to scan | `"https://app.example.com"` |
| `business_goal` | Tell it what matters most to your business | `"Focus on checkout and onboarding"` |
| `profile_name` | Use a logged-in account to scan private pages | `"my-auth-profile"` |
| `max_depth` | How deep to crawl (1 = homepage only, 2 = homepage + linked pages) | `2` |
| `max_pages` | Max pages to crawl before planning flows | `20` |
| `seed_urls` | Start crawl from specific same-origin URLs | `["https://app.example.com/pricing"]` |
| `include_url_pattern` | Regex: only crawl URLs that match | `"/(pricing|signup)"` |
| `exclude_url_pattern` | Regex: skip noisy URLs | `"/(blog|legal)"` |
| `return_inventory` | Include raw crawl inventory in response | `true` |
| `command` | Free-text instruction — blop figures out the rest | `"Discover auth flows for the dashboard"` |

**What you get back:** Flows include a `business_criticality` hint (`revenue`, `activation`, `retention`, `support`, `other`) so you can prioritize recording and triage results.

```json
{
  "flows": [
    {
      "flow_name": "user_login",
      "goal": "User logs in with email and password and reaches the dashboard",
      "severity_if_broken": "blocker",
      "confidence": 0.92,
      "business_criticality": "activation"
    },
    {
      "flow_name": "pricing_page_upgrade",
      "goal": "Visitor views pricing, clicks Pro plan CTA, reaches checkout",
      "severity_if_broken": "high",
      "confidence": 0.85,
      "business_criticality": "revenue"
    }
  ],
  "inventory_summary": {
    "auth_signals": ["sign in", "/login"],
    "business_signals": ["pricing", "checkout"]
  },
  "quality": {
    "passed": true,
    "warnings": []
  }
}
```

---

### 2. `explore_site_inventory` — *"Map the interface before planning tests"*

Runs inventory-only discovery (no Gemini flow planning) so you can inspect routes, forms, headings, auth signals, and business signals first. It now also includes `page_structures`, a compact per-page list of interactive ARIA nodes (`role` + `name`) to give agents layout context.

```
explore_site_inventory(
  app_url="https://your-app.com",
  max_depth=2,
  max_pages=20,
  include_url_pattern="/(pricing|signup|dashboard)"
)
```

Use this when you want deterministic topology mapping before `discover_test_flows`.

---

### 3. `get_page_structure` — *"Give me structure for one route right now"*

Captures a single-page interactive structure snapshot using Playwright's accessibility tree. Useful before recording or debugging when you want context for one URL without running a full crawl.

```
get_page_structure(
  app_url="https://your-app.com",
  url="https://your-app.com/pricing",  # optional; defaults to app_url
  profile_name="my-auth-profile"       # optional
)
```

Returns a flattened `interactive_nodes` list so MCP agents can reason about what controls are available before choosing actions.

---

### 4. `save_auth_profile` — *"Here are my login credentials"*

Saves your login details so blop can test pages that require being signed in. Your password is only stored locally on your machine.

**Basic usage (username + password from your .env file):**
```
save_auth_profile(
  profile_name="my-app-login",
  auth_type="env_login",
  login_url="https://your-app.com/login"
)
```

**Auth types explained:**

| Type | When to use | Example |
|------|-------------|---------|
| `env_login` | You have a username + password | Standard email/password login |
| `storage_state` | You have a browser session file from Playwright | SSO, OAuth, MFA flows |
| `cookie_json` | You have exported browser cookies | When you can't automate login |

**Tips:**
- blop caches sessions for 1 hour — it won't re-login every time you run tests
- Your credentials are read from environment variables, never stored as plain text in the database
- For SSO/Google login, use `storage_state` or the interactive **`capture_auth_session`** tool (see below)
- Use `user_data_dir` when the login provider (e.g. Google, LinkedIn) blocks fresh browser contexts — blop will use a persistent Chromium profile

---

### 5. `capture_auth_session` — *"Log in once in a browser, I'll save the session"*

Opens a **visible** browser at your login URL. You complete Google/GitHub OAuth, MFA, or any flow by hand. The tool polls the page URL every 500ms and, when it detects success, saves the Playwright storage state and creates an auth profile automatically. No manual session export needed.

**Basic usage:**
```
capture_auth_session(
  profile_name="my-app-sso",
  login_url="https://your-app.com/login",
  success_url_pattern="/dashboard"
)
```

**Parameters:**
| Parameter | What it does | Example |
|-----------|-------------|---------|
| `profile_name` | Name for the saved auth profile | `"my-app-sso"` |
| `login_url` | URL to open (your app's login or OAuth start) | `"https://app.example.com/login"` |
| `success_url_pattern` | URL substring that means "logged in" (optional) | `"/dashboard"` — if omitted, any URL change away from the login page counts as success |
| `timeout_secs` | Max seconds to wait for you to complete login (default 120) | `180` |
| `user_data_dir` | Path to a persistent Chromium profile dir (optional) | `.blop/chrome_profile_myapp` — use when OAuth providers treat a fresh browser as a bot |

**Returns:** `status` is `"captured"` (session saved, profile ready for `record_test_flow` and `run_regression_test`) or `"timeout"` (no success detected in time). On success you get `storage_state_path`; the profile is already stored — just pass `profile_name` to other tools.

---

<a id="evaluate-web-task"></a>
### `evaluate_web_task` — *"Run one exploratory task and get a full report now"*

Runs a one-shot browser agent evaluation and returns results immediately in the same call. Use this for exploratory QA checks, quick validation, and ad-hoc investigation. If you want a reusable regression artifact (`flow_id`) for later replay, use `record_test_flow` instead.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `app_url` | `str` | required | Base URL to test. |
| `task` | `str` | required | Natural-language objective for the agent. |
| `profile_name` | `str \| null` | `null` | Optional saved auth profile to run as a logged-in user. |
| `headless` | `bool` | `false` | Run browser headless or visible. |
| `max_steps` | `int` | `25` | Agent action budget before termination. |
| `capture` | `list[str] \| null` | `["screenshots","console","network","trace"]` | Evidence channels to persist (`screenshots`, `console`, `network`, `trace`). Invalid values are ignored. |
| `format` | `str` | `"markdown"` | Output format: `markdown`, `text`, or `json`. |
| `save_as_recorded_flow` | `bool` | `false` | Whether to promote successful agent actions into a saved `RecordedFlow`. |
| `flow_name` | `str \| null` | `null` | Optional flow name when `save_as_recorded_flow=true`. |

**Return schema (high level):**

- `status`: `"completed"` on normal completion, otherwise error status.
- `success`: boolean pass/fail signal from evaluation outcome.
- `pass_fail`: normalized result (`pass`, `fail`, or `error`).
- `metrics`: execution stats such as elapsed time and step counts.
- `agent_steps`: normalized step summaries (`step`, `action`, `description`).
- `evidence`: logs/artifacts (`console_errors`, `network_failures`, screenshots, trace paths when captured).
- `error`: populated when input validation/auth/bootstrap/agent execution fails.
- `report`: formatted text/markdown block when `format` requests human-readable output.

**Side effects:**

- Writes run artifacts to local storage (`runs/...`) when evidence capture is enabled.
- Persists run metadata and health events in the local SQLite store.
- Optionally creates and saves a reusable recorded flow when `save_as_recorded_flow=true`.

**Example call:**
```python
evaluate_web_task(
  app_url="https://your-app.com",
  task="Open pricing, click Pro plan CTA, and verify checkout loads",
  profile_name="my-app-login",
  max_steps=30,
  capture=["screenshots", "console", "network"],
  format="json"
)
```

**Example output (abridged):**
```json
{
  "status": "completed",
  "success": true,
  "pass_fail": "pass",
  "metrics": {"elapsed_secs": 18.4, "steps_taken": 9},
  "agent_steps": [
    {"step": 1, "action": "navigate", "description": "Navigate -> https://your-app.com/pricing"},
    {"step": 2, "action": "click_element", "description": "Click element (index 4)"}
  ],
  "evidence": {"console_errors": [], "network_failures": [], "screenshots": ["..."]},
  "error": null
}
```

---

### 6. `record_test_flow` — *"Watch and learn this flow"*

Runs an AI agent in a real browser to accomplish a goal, and saves every step it takes. You can then replay this recording as many times as you want.

Use `record_test_flow` when you want a reusable regression artifact (`flow_id`). For one-off exploratory checks, use [`evaluate_web_task`](#evaluate-web-task).

**Basic usage:**
```
record_test_flow(
  app_url="https://your-app.com",
  flow_name="user_signup",
  goal="Sign up for a new account with email and verify the welcome screen appears"
)
```

**With authentication and business criticality (for flows behind login):**
```
record_test_flow(
  app_url="https://your-app.com",
  flow_name="create_new_project",
  goal="Log in, create a new project called 'Test Project', and verify it appears in the project list",
  profile_name="my-app-login",
  business_criticality="revenue"
)
```

**`business_criticality`** (optional) is one of `revenue`, `activation`, `retention`, `support`, `other`. It is used in results and severity labels (e.g. "BLOCKER in revenue flow: checkout") so you can triage by business impact.

**Writing good goals — what makes the difference:**

| ❌ Vague (gets generic results) | ✅ Specific (gets reliable tests) |
|--------------------------------|----------------------------------|
| `"Test the login"` | `"Log in with email and password, verify the dashboard shows the user's name in the top right corner"` |
| `"Check pricing"` | `"Navigate to pricing, verify Free, Pro ($35/mo), and Enterprise tiers are visible, click the Pro CTA and confirm it leads to checkout"` |
| `"Test the form"` | `"Fill in the contact form with name, company email, and message, submit it, and verify a confirmation message appears"` |

**What gets captured per step:**
- The element clicked or filled (selector + visible text)
- A screenshot at that moment
- The URL before and after
- Final assertions generated by AI from the end state

---

### 7. `run_regression_test` — *"Check if everything still works"*

Replays recorded flows against your app. Returns immediately with a `run_id` — poll for results with `get_test_results`. Run status moves: `queued` → `running` → (`completed` | `failed` | `cancelled`). If the auth profile cannot be resolved, status is `waiting_auth` and no flows run until you fix the profile and retry.

`flow_ids` must come from `record_test_flow` (or `list_recorded_tests`) and all IDs must be valid for the run to start.

**Basic usage:**
```
run_regression_test(
  app_url="https://your-app.com",
  flow_ids=["abc123", "def456"]
)
```

**With auth and hybrid mode:**
```
run_regression_test(
  app_url="https://your-app.com",
  flow_ids=["abc123", "def456"],
  profile_name="my-app-login",
  run_mode="hybrid"
)
```

**Run modes explained:**

| Mode | What it does | When to use |
|------|-------------|-------------|
| `hybrid` *(default)* | Tries saved steps first; if a selector breaks, AI repairs that single step | Best for most replay runs |
| `strict_steps` | Follows saved steps exactly — fails immediately if anything doesn't match | CI/CD where you want strict enforcement |
| `goal_fallback` | Ignores saved steps, replays using only the original goal description | Drift recovery only, not the normal release gate |

For release gating, prefer `run_release_check(..., mode="replay")` with recorded `flow_ids`. `goal_fallback` is useful for recovery and diagnosis when a recorded flow has drifted too far, but it should not be the default ship/no-ship path.

---

### 8. `get_test_results` — *"What broke?"*

Polls a running test and returns structured results. Call it repeatedly every 2-5 seconds until `status` is `"completed"`, `"failed"`, `"cancelled"`, or `"waiting_auth"`. When status is `waiting_auth`, the response includes `waiting_auth_message` explaining that the auth profile could not be resolved (check credentials or re-run `capture_auth_session` if the session expired).

```
get_test_results("your-run-id-here")
```

**Understanding the results:**

```json
{
  "status": "completed",
  "severity_counts": {
    "blocker": 1,
    "high": 0,
    "medium": 2,
    "pass": 4
  },
  "failed_cases": [
    {
      "flow_name": "checkout_flow",
      "severity": "blocker",
      "replay_mode": "hybrid_repair",
      "step_failure_index": 3,
      "assertion_failures": ["Payment page should load after clicking Subscribe"],
      "repro_steps": ["Go to /pricing", "Click 'Get Pro Plan'", "Observe: redirected to homepage instead of checkout"]
    }
  ]
}
```

**Severity levels — what they mean for your business:**

| Level | Meaning | Action |
|-------|---------|--------|
| 🔴 **blocker** | Core workflow is completely broken. Users can't complete a key task. | Fix before shipping anything |
| 🟠 **high** | Major feature broken, significant user impact | Fix in current sprint |
| 🟡 **medium** | Partial issue, workaround possible | Fix in next sprint |
| 🟢 **low** | Cosmetic or edge case | Backlog |
| ✅ **pass** | Everything worked | No action needed |

**`replay_mode` tells you how the test ran:**
- `strict_steps` — selector matched, step ran exactly as recorded
- `hybrid_repair` — original selector broke, AI found the element another way
- `goal_fallback` — step-by-step replay failed entirely, fell back to full agent replay

Look for replay trust cues in the response:
- `replay_trust_summary` tells you whether replay stayed on the golden path or needs manual review.
- `failure_classification` explains whether the failure looks like auth, drift, infra, or product.
- `stale_flow_guidance` appears when an old recording should be refreshed before trusting the failure.

---

### 9. `list_runs` — *"What runs are active or recent?"*

Lists recent regression runs, optionally filtered by status.

```
list_runs(limit=20, status="running")
```

Useful when you lost a `run_id` or need to inspect background runs.

---

### 10. `get_run_health_stream` — *"What happened during this run, step by step?"*

Returns control-plane run events (queued, started, per-case completion, completed/failed) so you can inspect lifecycle and timing without opening artifacts first.

```
get_run_health_stream(
  run_id="your-run-id",
  limit=500  # optional
)
```

Useful for quick triage when a run exits unexpectedly or when you want to inspect replay/healing metadata at event granularity.

---

### 11. `get_risk_analytics` — *"Where are our biggest regression risks?"*

Aggregates recent runs into high-signal diagnostics:
- flaky step leaderboard
- top failing transitions
- failure rates by `business_criticality` (revenue/activation/retention/support/other)

```
get_risk_analytics(limit_runs=30)
```

Use this to prioritize stabilization work across many runs instead of triaging one run at a time.

---

### 12. `list_recorded_tests` — *"What tests do I have?"*

Lists every flow you've ever recorded.

```
list_recorded_tests()
```

Returns a list with `flow_id`, `flow_name`, `app_url`, `goal`, and `created_at`. Use the `flow_id` values in `run_regression_test`.

---

### 13. `debug_test_case` — *"Why exactly did this fail?"*

Re-runs a specific failed case in a visible browser window (not headless), captures every screenshot, and generates a plain-English explanation of what went wrong.

```
debug_test_case(
  run_id="your-run-id",
  case_id="the-failed-case-id"
)
```

**What you get back:**
```json
{
  "status": "fail",
  "step_failure_index": 3,
  "replay_mode": "hybrid_repair",
  "assertion_failures": ["Dashboard should show user inbox after login"],
  "why_failed": "The login succeeded but the session cookie was not persisted between the auth step and the dashboard navigation. The app redirected to /login again instead of /dashboard.",
  "repro_steps": ["Navigate to /auth", "Fill email and password", "Click Sign In", "Session lost — redirect back to /auth"],
  "screenshots": ["runs/screenshots/run123/case456/step_003.png"]
}
```

---

### 14. `validate_setup` — *"Is everything ready to run tests?"*

Checks preconditions before you run flows: `GOOGLE_API_KEY`, Chromium installed, SQLite DB, optional `app_url` reachability, and optional auth profile (including whether a `storage_state` session is still valid). Use it after changing env vars or before a big run.

```
validate_setup(app_url="https://your-app.com", profile_name="my-app-login")
```

**Returns:** `status` is `"ready"` (all checks passed), `"warnings"` (e.g. app URL unreachable but you can still run), or `"blocked"` (e.g. missing API key or Chromium). The `checks` array lists each condition and whether it passed; `blockers` and `warnings` give short messages. If an auth profile's session has expired, the message will suggest re-running `capture_auth_session`.

---

## MCP resources — low-token context for agents

blop now exposes read-only MCP resources so agents can pull structured context without triggering heavy tool workflows.

### `blop://inventory/{app}`

Latest saved inventory for an app URL.

- URL-encode the full app URL in `{app}`.
- Example:
  - `blop://inventory/https%3A%2F%2Fapp.example.com`

### `blop://context-graph/{app}`

Latest persisted `SiteContextGraph` snapshot (nodes, edges, archetype, freshness/confidence metadata).

- Example:
  - `blop://context-graph/https%3A%2F%2Fapp.example.com`

### `blop://run/{run_id}/artifact-index`

Artifact index for a run (artifact metadata + case ids), useful before drilling into screenshots/traces.

- Example:
  - `blop://run/abc123/artifact-index`

### `blop://flow/{flow_id}/stability-profile`

Flow-level stability profile derived from historical cases (pass/failure rates, replay-mode distribution, stability score).

- Example:
  - `blop://flow/def456/stability-profile`

### Recommended context-first workflow

1. Read `inventory` + `context-graph` resources.
2. Run `discover_test_flows`/`record_test_flow` using that context.
3. Run `run_regression_test`.
4. Read `artifact-index` + `stability-profile`.
5. Use `get_risk_analytics` for cross-run prioritization.

---

## MCP v2 surface (control plane)

blop v2 expands beyond regression execution into **change intelligence**, **journey health**, **incident clustering**, and **remediation orchestration**.

### New v2 tools

- `blop_v2_get_surface_contract` — returns machine-readable request/response schemas + examples for all v2 tools.
- `blop_v2_capture_context` — captures a context graph snapshot and structural diff summary.
- `blop_v2_compare_context` — compares two graph versions and returns structural/business impact.
- `blop_v2_assess_release_risk` — release-level risk score and top risks from context/run evidence.
- `blop_v2_get_journey_health` — SLO-like health view for business journeys over time.
- `blop_v2_cluster_incidents` — deduplicates failures into incident clusters with blast radius.
- `blop_v2_generate_remediation` — emits issue-ready remediation drafts (repro + evidence).
- `blop_v2_ingest_telemetry_signals` — ingests external signals (error rate/latency/conversion).
- `blop_v2_get_correlation_report` — correlates failures with telemetry changes for prioritization.

### New v2 resources

- `blop://v2/contracts/tools`
- `blop://v2/context/{urlencoded_app_url}/latest`
- `blop://v2/context/{urlencoded_app_url}/history/{limit}`
- `blop://v2/context/{urlencoded_app_url}/diff/{baseline_graph_id}/{candidate_graph_id}`
- `blop://v2/release/{release_id}/risk-summary`
- `blop://v2/journey/{urlencoded_app_url}/health/{window}` (`window`: `24h`, `7d`, `30d`)
- `blop://v2/incidents/{urlencoded_app_url}/open`
- `blop://v2/incident/{cluster_id}`
- `blop://v2/incident/{cluster_id}/remediation-draft`
- `blop://v2/correlation/{urlencoded_app_url}/{window}`

### Compatibility strategy (v1 + v2)

- v1 tools remain supported (`discover_test_flows`, `run_regression_test`, `get_test_results`, etc.).
- v1 responses now include `related_v2_resources` links so agents can progressively adopt v2 context.
- v2 resources use a stable envelope:

```json
{
  "resource_version": "v2",
  "generated_at": "2026-03-18T12:00:00Z",
  "app_url": "https://app.example.com",
  "data": {}
}
```

---

## How auth profiles work

```
Your .env file                     blop
─────────────────────────────────────────────────────────────
TEST_USERNAME=user@company.com  →  Reads at runtime
TEST_PASSWORD=secret            →  Never stored in DB
                                   ↓
                              Opens login page
                              Fills credentials
                              Saves session cookie
                                   ↓
                         .blop/auth_state_profile.json
                         (valid for 1 hour, then re-logs in)
```

blop tries these login field selectors automatically, in order:
1. `input[name="username"]`
2. `input[name="email"]`
3. `input[type="email"]`
4. `#email`
5. Any input with "email" in the placeholder

You can override with env vars `TEST_USERNAME_SELECTOR` and `TEST_PASSWORD_SELECTOR` if your login form is unusual.

---

## Where your test data lives

```
your-project/
├── .env                          ← Your credentials (never commit this)
├── .blop/
│   ├── runs.db                   ← All test history (SQLite)
│   └── auth_state_*.json         ← Cached login sessions
└── runs/
    ├── screenshots/
    │   └── <run_id>/<case_id>/
    │       ├── step_000.png       ← Screenshot at each step
    │       └── step_001.png
    ├── traces/
    │   └── <run_id>/<case_id>.zip ← Playwright trace (open with trace viewer)
    └── console/
        └── <run_id>/<case_id>.log ← Browser console errors
```

---

## Troubleshooting

**"blop not found" or "command not found"**
Make sure you've activated the virtual environment: `source .venv/bin/activate`

**"GOOGLE_API_KEY not set"**
Check your `.env` file exists in the blop-mcp folder and has your key on the `GOOGLE_API_KEY=` line. Alternatively, set it directly in the MCP config JSON.

**Login keeps failing**
1. Double-check your `TEST_USERNAME` and `TEST_PASSWORD` in `.env`
2. Try visiting your `LOGIN_URL` manually to confirm the credentials work
3. If your login uses unusual field names, add `TEST_USERNAME_SELECTOR=input[name="your-field"]` to `.env`
4. For SSO/MFA, use `capture_auth_session` (opens a browser so you log in once; session is saved automatically) or `auth_type="storage_state"` with an exported session file
5. Run `validate_setup(profile_name="your-profile")` to verify the profile; if the session expired, re-run `capture_auth_session` or refresh your storage state file

**Tests are all passing but you know something is broken**
The regression engine uses AI vision to evaluate assertions — it shouldn't produce false positives. If something is marked pass that looks wrong, use `debug_test_case` to re-run it with full screenshot capture and see what the browser actually showed.

**"MCP server not connected" in Cursor**
1. Check the path in `mcp.json` points to where you actually cloned blop-mcp
2. Make sure `uv` is installed (`which uv` in Terminal)
3. Restart Cursor fully (Cmd+Q, reopen)

**The browser opens but does nothing / hangs**
Your `BLOP_MAX_STEPS` limit (default 50) may be too low for complex flows. Add `BLOP_MAX_STEPS=100` to `.env`.

---

## Exploration profiles (simple but flexible)

blop uses a deterministic-first architecture with adaptive repair fallback. For easier tuning across different interfaces, you can choose a profile:

- `BLOP_EXPLORATION_PROFILE=default` — balanced defaults for most apps.
- `BLOP_EXPLORATION_PROFILE=saas_marketing` — tuned for async SPAs, heavy client-side editors, and cross-origin handoffs like `rendley.com` → `app.rendley.com`.

You can still override individual knobs with env vars (`BLOP_NETWORK_IDLE_WAIT`, `BLOP_SPA_SETTLE_MS`, `BLOP_AGENT_MAX_FAILURES`, `BLOP_AGENT_MAX_ACTIONS_PER_STEP`, `BLOP_DISCOVERY_MAX_PAGES`).

Design baseline references:
- Playwright deterministic web-first model: [Playwright intro](https://playwright.dev/docs/intro), [playwright-mcp](https://github.com/microsoft/playwright-mcp)
- Agentic fallback model: [browser-use](https://github.com/browser-use/browser-use)
- Healing + observability lifecycle: [TestSprite create tests](https://docs.testsprite.com/mcp/core/create-tests-new-project), [test lifecycle](https://docs.testsprite.com/mcp/concepts/test-type-lifecycle), [healing](https://docs.testsprite.com/mcp/concepts/healing-observability), [dashboard](https://docs.testsprite.com/mcp/core/test-progress-dashboard)
- MCP server/tool contract design: [server concepts](https://modelcontextprotocol.io/docs/learn/server-concepts), [build server](https://modelcontextprotocol.io/docs/develop/build-server)

---

## Environment variables — full reference

| Variable | Required | Default | What it does |
|----------|----------|---------|-------------|
| `GOOGLE_API_KEY` | **Yes** | — | Gemini API key. Get one free at [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `APP_BASE_URL` | No | — | Default app URL (used as fallback if no URL passed to tools) |
| `LOGIN_URL` | No | — | Where blop navigates to log in |
| `TEST_USERNAME` | No | — | Login email/username |
| `TEST_PASSWORD` | No | — | Login password |
| `TEST_USERNAME_SELECTOR` | No | auto-detected | CSS selector for the username input field |
| `TEST_PASSWORD_SELECTOR` | No | auto-detected | CSS selector for the password input field |
| `STORAGE_STATE_PATH` | No | — | Path to a saved Playwright session (for SSO/OAuth) |
| `COOKIE_JSON_PATH` | No | — | Path to exported browser cookies (JSON array) |
| `BLOP_DB_PATH` | No | `.blop/runs.db` | Where blop stores its database |
| `BLOP_HEADLESS` | No | `true` | `false` = show browser window during tests (useful for debugging) |
| `BLOP_MAX_STEPS` | No | `50` | Max steps the AI agent takes per flow |
| `BLOP_ALLOW_SCREENSHOT_LLM` | No | `false` | Privacy guard for visual-regression triage. When `false`, baseline/current screenshots are never base64-encoded or sent to external LLMs. |
| `BLOP_ENV` | No | `development` | Environment mode (`production` enables stricter validation expectations) |
| `BLOP_REQUIRE_ABSOLUTE_PATHS` | No | `false` (`true` in production recommended) | Require absolute paths for DB/runs/log values |
| `BLOP_ALLOW_INTERNAL_URLS` | No | `false` | Block private/internal app URLs unless explicitly enabled |
| `BLOP_ALLOWED_HOSTS` | No | — | Optional host allowlist for `app_url` validation |
| `BLOP_RUN_TIMEOUT_SECS` | No | `0` | Total run timeout in seconds (`0` disables timeout) |
| `BLOP_STEP_TIMEOUT_SECS` | No | `45` | Per-step replay timeout in seconds |
| `BLOP_DEBUG_LOG` | No | `.blop/blop.log` | JSON log destination path |
| `BLOP_CAPABILITIES_PROFILE` | No | env-dependent | Predefined capability profile (`production_minimal`, `production_debug`, `full`) |
| `BLOP_ENABLE_COMPAT_TOOLS` | No | `false` | Registers legacy/compat MCP tool surface when `true` |
| `BLOP_EXPLORATION_PROFILE` | No | `default` | Tuning preset (`default` or `saas_marketing`) for discovery and replay behavior |
| `BLOP_DISCOVERY_MAX_PAGES` | No | profile-driven | Default crawl page cap for discovery tools |
| `BLOP_AGENT_MAX_FAILURES` | No | profile-driven | Max recoverable action failures before agent aborts recording |
| `BLOP_AGENT_MAX_ACTIONS_PER_STEP` | No | profile-driven | Max agent actions per reasoning step during recording |
| `BLOP_NETWORK_IDLE_WAIT` | No | `2.0` | Seconds to wait for network idle after page load (increase for WebGL/WASM or slow dashboards) |
| `BLOP_SPA_SETTLE_MS` | No | `1500` | Extra settle time in ms after SPA navigation (for pushState / client-side routing) |

---

Powered by [Browser Use](https://github.com/browser-use/browser-use) and [Google Gemini](https://ai.google.dev/)

---

## Origins / Attribution

blop was initially developed as a fork of [browser-use/vibetest-use](https://github.com/browser-use/vibetest-use). The codebase has since been entirely rewritten with a new architecture, engine, tool surface, and storage layer. This repository (`blop-mcp`) is the canonical home for blop going forward.

If the upstream vibetest-use project's license requires attribution, see the [upstream repository](https://github.com/browser-use/vibetest-use) for license details.
