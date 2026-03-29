"""MVP prompt constants for release confidence workflows."""

RELEASE_READINESS_REVIEW = """\
Run a release-confidence review for a web application.

Step 1 — Validate setup:
   validate_release_setup(app_url="https://your-app.com", profile_name="your_profile")
   → Fix any blockers before proceeding.

Step 2 — Discover critical journeys:
   discover_critical_journeys(app_url="https://your-app.com", business_goal="SaaS product with checkout")
   → Review journeys and focus on include_in_release_gating=true.

Step 3 — Confirm the release-gating journey inventory:
   blop://journeys
   → Verify the highest-value journeys already have recorded coverage.

Step 4 — Record or refresh any missing release-gating journeys:
   record_test_flow(
     app_url="https://your-app.com",
     flow_name="checkout_flow",
     goal="Complete a purchase end-to-end",
     business_criticality="revenue"
   )
   → Transitional admin step. Repeat only for the release-gating journeys you need, and refresh stale recordings before trusting replay failures.

Step 5 — Run release check:
   run_release_check(app_url="https://your-app.com", mode="replay")
   → This queues a run. Note the run_id and release_id returned.
   Optional advisory preflight:
   run_release_check(app_url="https://your-app.com", mode="replay", smoke_preflight=True)
   → Runs a bounded smoke sweep first; replay remains the authoritative release gate.

Step 6 — Poll for results:
   get_test_results(run_id="<run_id from step 4>")
   → Wait until status is "completed" or "failed".

Step 7 — Review the release brief:
   blop://release/{release_id}/brief
   → Check the decision, risk score, blocker journeys, and top actions.

Step 8 — Triage any blockers:
   triage_release_blocker(release_id="<release_id>")
   → Review likely_cause, evidence_summary, and recommended_action.

Step 9 — Make the ship/no-ship decision:
   - SHIP: All critical journeys passed, no blockers.
   - INVESTIGATE: Issues detected, review evidence with engineering.
   - BLOCK: Blocker-severity failures in revenue or activation journeys — do not ship.

Note:
   replay mode over recorded journeys is the release-gating golden path.
   targeted mode is a scoped fallback for fast smoke checks when replay coverage is incomplete.
   goal_fallback is a recovery mode for drift, not the default release gate.

Resources available:
   blop://journeys                        → All recorded journeys
   blop://release/{release_id}/brief      → Condensed release summary
   blop://release/{release_id}/artifacts  → Screenshots, traces, console logs
   blop://release/{release_id}/incidents  → Incident clusters linked to this release
"""

INVESTIGATE_BLOCKER = """\
Investigate a specific release blocker and turn it into an actionable fix plan.

Provide one of: run_id, release_id, journey_id, or incident_cluster_id.

Usage:
   triage_release_blocker(run_id="<run_id>")
   triage_release_blocker(journey_id="<flow_id>")
   triage_release_blocker(release_id="<release_id>")
   triage_release_blocker(incident_cluster_id="<cluster_id>")

The tool returns:
   - likely_cause: the probable root cause
   - evidence_summary: condensed repro steps, console errors, assertion failures
   - user_business_impact: which business outcomes are affected
   - recommended_action: the highest-priority fix
   - suggested_owner: engineering team or person to notify
   - linked_artifacts: screenshot and trace paths for inspection

Follow-up:
   - Review release evidence:
     blop://release/{release_id}/artifacts
   - Review linked incidents:
     blop://release/{release_id}/incidents
   - If deeper case-level debugging is needed, use the internal debug workflow.
"""

EXPLAIN_RELEASE_RISK = """\
Explain a release-confidence result in plain language for a non-technical stakeholder.

Usage:
   1. Read the release brief:
      blop://release/{release_id}/brief

   2. Translate the result to business language:
      - SHIP (low risk): "All critical user journeys passed. Safe to release."
      - INVESTIGATE (medium risk): "Some issues were detected. Engineering should review
        before releasing to all users. Consider a staged rollout."
      - BLOCK (high/blocker risk): "Critical flows are broken. Do not release until
        the listed blockers are resolved."

   3. For each blocker journey, explain the user impact:
      triage_release_blocker(release_id="<release_id>")
      → use user_business_impact field from the response

   Key metrics to communicate:
      - Blocker count: number of journeys with severity=blocker
      - Critical journey failures: failures in revenue or activation flows
      - Confidence: how reliable the current release recommendation is
"""
