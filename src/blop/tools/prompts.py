"""MVP prompt constants for release confidence workflows."""

RELEASE_READINESS_REVIEW = """\
Run a full release confidence review for a web application.

Step 1 — Validate setup:
   validate_release_setup(app_url="https://your-app.com", profile_name="your_profile")
   → Fix any blockers before proceeding.

Step 2 — Discover critical journeys:
   discover_critical_journeys(app_url="https://your-app.com", business_goal="SaaS product with checkout")
   → Review journeys. Note which have include_in_release_gating=true.

Step 3 — Record or refresh the gated journeys:
   record_test_flow(
     app_url="https://your-app.com",
     flow_name="checkout_flow",
     goal="Complete a purchase end-to-end",
     business_criticality="revenue"
   )
   → Repeat for each gated journey you want to use for release decisions.

Step 4 — Run release check:
   run_release_check(app_url="https://your-app.com", mode="replay")
   → This queues a run. Note the run_id and release_id returned.

Step 5 — Poll for results:
   get_test_results(run_id="<run_id from step 4>")
   → Wait until status is "completed" or "failed".

Step 6 — Triage any blockers:
   triage_release_blocker(run_id="<run_id>")
   → Review likely_cause, evidence_summary, and recommended_action.

Step 7 — Make the ship/no-ship decision:
   - SHIP: All critical journeys passed, no blockers.
   - INVESTIGATE: Issues detected, review evidence with engineering.
   - BLOCK: Blocker-severity failures in revenue or activation journeys — do not ship.

Note:
   targeted mode is useful for one-off smoke checks, but replay mode over recorded flows is the release-gating golden path.

Resources available:
   blop://journeys                        → All recorded journeys
   blop://release/{release_id}/brief      → Condensed release summary
   blop://release/{release_id}/artifacts  → Screenshots, traces, console logs
   blop://release/{release_id}/incidents  → Incident clusters linked to this release
"""

INVESTIGATE_BLOCKER = """\
Investigate a specific release blocker and generate a triage report.

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
   - To re-run the failing case with verbose logging:
     debug_test_case(case_id="<case_id>")
   - To cluster related failures:
     blop_v2_cluster_incidents(app_url="<app_url>", window="24h")
   - To generate a full remediation draft:
     blop_v2_generate_remediation(cluster_id="<cluster_id>", app_url="<app_url>")
"""

EXPLAIN_RELEASE_RISK = """\
Explain a release risk score in plain language for a non-technical stakeholder.

Usage:
   1. Get the release check result:
      get_test_results(run_id="<run_id>")

   2. Note the release_recommendation field:
      - decision: SHIP / INVESTIGATE / BLOCK
      - confidence: high / medium / low
      - rationale: plain-language explanation

   3. Translate risk level to business language:
      - SHIP (low risk): "All critical user journeys passed. Safe to release."
      - INVESTIGATE (medium risk): "Some issues were detected. Engineering should review
        before releasing to all users. Consider a staged rollout."
      - BLOCK (high/blocker risk): "Critical flows are broken. Do not release until
        the listed blockers are resolved."

   4. For each blocker journey, explain the user impact:
      triage_release_blocker(run_id="<run_id>")
      → use user_business_impact field from the response

   Key metrics to communicate:
      - Blocker count: number of journeys with severity=blocker
      - Critical journey failures: failures in revenue or activation flows
      - Confidence: how reliably blop can make this determination
"""
