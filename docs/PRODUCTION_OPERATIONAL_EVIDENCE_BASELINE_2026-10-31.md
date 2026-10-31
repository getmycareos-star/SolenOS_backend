# SolenOS production operational evidence baseline — 2026-10-31

Status: observational Railway HTTP-log snapshot. Not an SLO report, reliability guarantee, or representative user-traffic study.

## Observation window and source

- Requested window: 2026-10-30 12:15 to 2026-10-31 12:15, Africa/Lagos (UTC+01:00).
- Source: Railway HTTP request log stream for the SolenOS_backend production service.
- Returned records: 317 HTTP requests.
- Main at capture: d128ccaf9b06ee31e348e3adad2ef9ff74db20a3.
- Deployment aed8afa4-8579-4688-8373-7eff1e7e4b05 for that commit was BUILDING. Prior successful deployment: 4166d18b-8c2a-4273-ae7a-15db2d426a9b, commit 17b012ffb5341745764288553cabaed52a8c1b56. Do not treat the newer build as live until Railway reports SUCCESS and the active replica is verified.

## HTTP results

| Status | Count |
|---|---:|
| 200 | 233 |
| 201 | 11 |
| 401 | 37 |
| 403 | 21 |
| 404 | 10 |
| 503 | 5 |
| Total | 317 |

All five observed 5xx responses were 503 on POST /api/v1/conversation/message. Durations: 1,618 ms, 3,534 ms, 3,643 ms, 4,767 ms, and 13,105 ms. This is a concrete production failure signal requiring investigation. The status code alone does not establish its cause.

The 68 4xx responses are not automatically service failures. They include authorization responses and route probes; classify against expected endpoint contracts before calling them incidents.

## Request latency

| Percentile | Duration |
|---|---:|
| p50 | 160 ms |
| p95 | 1,618 ms |
| p99 | 3,604 ms |
| Maximum | 13,105 ms |

Nineteen requests exceeded one second. Several were readiness/OpenAPI probes; the maximum was a 503 conversation request. These mixed-endpoint percentiles are not caregiver workflow latency.

## Traffic and service-state limitations

User-agent heuristics classified records as 185 browser, 60 curl/monitor, 40 automation/headless/test-like, and 32 other/unknown. User-agent strings do not prove human identity or intent. These 317 requests cannot be represented as 317 caregiver sessions.

No IP addresses, raw user agents, person/caregiver IDs, request bodies, or care content are included in this report.

PostgreSQL was online with one replica. The backend service had a newer deployment in BUILDING state and an earlier successful deployment. No active warning/critical notification was reported. Railway reported 112 recent failed deployment attempts in its 24-hour window; these are deployment failures, not HTTP failures, and must not be added to the five 503s.

The existing production-readiness workflow runs every 15 minutes and checks health, readiness, migration/schema state, required continuity routes, and CORS preflight. It does not currently provide a durable request-level 5xx/latency time series.

## Interpretation

This snapshot establishes production requests and five 503 responses on one endpoint during the observed window. It does not establish sustained availability, successful care-record write/read integrity under real use, recovery after production database/process failure, or a reliable caregiver workflow error rate. There is no denominator for completed caregiver workflows.

## Required evidence going forward

Collect daily privacy-preserving summaries of:
- Request count by route template and status class, never raw path identifiers.
- 5xx count/rate and endpoint-specific failure rate.
- p50/p95/p99 latency by workflow route.
- Readiness, migration head, schema verification, and replica state.
- Database connection failures, transaction rollbacks, deadlocks, and pool exhaustion.
- Durable write/fresh-session read checks using dedicated synthetic canary identities only.
- Restart/redeploy recovery checks with timestamped pre/post state and integrity assertions.
- Incident count, time to detect/recover, unresolved incident age, and deployment SHA.

Set SLO thresholds only after representative traffic and workflow denominators exist. Alert on unexpected 5xx for care-record reads/writes and any confirmed persistence, authorization, or cross-person isolation failure.

## Current status

- Platform log snapshot: collected.
- Sustained production observation: not established.
- Five conversation endpoint 503s: observed; root cause unresolved.
- Durable request-level trend/alerting: not established by current monitor.
- Production data-integrity canary across restart: not established by this snapshot.
- Real caregiver workflow success rate: not measurable from current logs.
