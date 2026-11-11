# SolenOS Security Incident Response & Breach Notification Procedure

**Status:** Operational procedure. Release status remains PARTIAL until named contacts, production containment rehearsal, evidence-preservation rehearsal and tabletop exercise are verified.
**Owner:** Founder/CEO acting as Incident Commander until a qualified delegate is formally appointed.
**Scope:** Frontend, backend, databases, identity/session services, file storage, AI providers, hosting/infrastructure providers, support channels and subprocessors.
**Review:** Quarterly and after every material incident or architecture/legal change.

This procedure is not a claim of HIPAA, FTC, NDPA/NDPC or other regulatory compliance. Applicability and notification duties must be assessed against the actual SolenOS entity, processing activities, affected people, contracts and jurisdictions with qualified counsel or a data-protection professional.

## 1. Response principles

Priorities: (1) protect affected people and stop ongoing exposure; (2) contain access without destroying evidence; (3) establish scope and preserve care-record integrity; (4) meet applicable legal and contractual duties; (5) restore secure operations and prevent recurrence.

Treat a suspected compromise as a potential confidentiality, integrity or availability incident until evidence supports a narrower classification. Do not wait for certainty before proportionate containment. Do not put passwords, bearer tokens, API keys, raw care notes or unnecessary personal data in tickets, logs or incident notes.

## 2. Reporting channel and ownership

### Incident Commander

The Founder/CEO is the default Incident Commander until a named delegate is recorded. The Incident Commander opens and owns the incident, assigns severity, coordinates investigation and containment, maintains the timeline, restricts evidence access, engages providers and qualified counsel, approves recovery and records closure.

If the suspected incident involves the Incident Commander's own actions, appoint an independent qualified reviewer.

### Required contacts — release configuration

Record and verify in a restricted operations contact register (not public source control):

- Primary incident contact: **not verified/configured**.
- Backup incident contact: **not verified/configured**.
- Monitored security-report email/intake channel: **not verified/configured**.
- Legal/privacy escalation contact: **not appointed/verified**.
- Hosting/database provider support path and account owner.
- AI and other subprocessor incident contacts.

Do not invent or publish an email address or treat an unmonitored inbox as an incident channel. Until primary and backup channels have been tested end-to-end, this gate is BLOCKED. Any report received through an existing SolenOS-controlled support channel must be preserved with its original timestamp and escalated immediately to the Incident Commander.

Report suspected unauthorized access, disclosure, alteration, deletion or loss; cross-family access; lost devices or exposed sessions; credential/API/database/infrastructure compromise; suspicious privileged access; malware/exfiltration/denial of service; accidental disclosure; AI/subprocessor exposure; and loss of auditability that prevents determining whether access occurred.

## 3. Severity and internal response targets

These are internal targets, not statutory notification deadlines.

| Severity | Examples | Response |
| --- | --- | --- |
| SEV-1 Critical | Confirmed cross-family access, ongoing/bulk exfiltration, production database or signing-key compromise | Contain immediately; Incident Commander engaged immediately; continuous response until contained |
| SEV-2 High | Credible unauthorized access to accounts/records, compromised caregiver session, exposed sensitive document or subprocessor incident | Immediate triage and containment/scope assessment without avoidable delay |
| SEV-3 Moderate | Blocked intrusion, isolated misconfiguration with no evidence of access, limited integrity/availability impact | Same-working-day triage and documented assessment |
| SEV-4 Low | False positive or security question with no identified impact | Record, assess and close with rationale |

Escalate when scope is unknown and sensitive care information may be involved. Do not downgrade solely because exfiltration has not yet been proven.

## 4. First-response checklist

1. Record discovery time in UTC and local timezone, reporter, detection source and concise factual description.
2. Create a restricted incident record and assign an incident ID.
3. Preserve the original report, request/correlation IDs, route templates, deployment SHA and provider references.
4. Notify the Incident Commander using the verified channel.
5. Classify provisionally and explicitly record unknowns.
6. Start proportionate containment if ongoing access/exposure is plausible.
7. Use internal record identifiers only where necessary; restrict the incident record.

Never investigate by using a caregiver's credentials, impersonating a caregiver, or downloading more care information than necessary.

## 5. Access containment and credential response

Use the narrowest action that reliably stops the threat. Preserve unrelated caregivers' access where safe.

### Anonymous sessions and caregiver access

- Revoke a compromised anonymous bearer session through the authenticated session-revocation route (POST /api/v1/sessions/revoke) when the token is available and the route is operational.
- If the token is unavailable or session middleware is suspect, use the controlled database/provider procedure to revoke the specific session record. Record operator, timestamp, reason and session identifier; never record the raw token.
- For account/device compromise, revoke affected sessions and trusted-device grants using implemented identity controls. Verify with a fresh request using the old credential; expected result is authorization denial.
- Suspend an account only through an existing, verified server-side control. If the affected identity type has no such control, isolate the relevant access path/service and track the missing control as P0.
- Never delete a caregiver or care-recipient record as a containment shortcut.

### Secrets and service isolation

Rotate reasonably suspected exposed database credentials, signing secrets, API keys, storage credentials and provider tokens in the provider's secret manager. Never commit secrets or put them in tickets. Inventory all dependent services, redeploy as required and prove old credentials no longer authenticate. Record failed dependencies and residual risk.

Disable compromised integrations or restrict egress where necessary. Assess whether queued prompts, files or data were already sent to the provider. If narrow containment cannot stop active exfiltration, take the affected route/feature/service offline and record customer impact and last known-good deployment reference.

## 6. Investigation and scope

Determine and document:

- Threat vector and initial access time or bounded estimate.
- Accounts, caregiver identities, sessions, devices, services and subprocessors involved.
- Care-recipient/family records and data categories potentially accessed.
- Whether data was viewed, queried, exported, downloaded, modified, deleted or transmitted.
- Successful, denied or indeterminate access attempts.
- Whether another family's record could be accessed.
- Whether AI prompts, embeddings, files, logs, backups or provider support bundles contained affected information.
- Exposure duration, affected-person count and counting method.
- Confidentiality, integrity and availability impact.
- Evidence gaps and confidence/limitations.

### Cross-family isolation investigation

For suspected authorization failure: identify actor/session and resource/action from request IDs and audit evidence; reproduce in a non-production environment with synthetic identities; test both authorized and unrelated identities; search the relevant route/time window; inspect caches, search, exports, summaries, AI context and file URLs; preserve results and deployment SHA. A denied request does not prove no earlier access succeeded. Missing logs do not prove access did not occur.

## 7. Evidence preservation and chain of custody

Preserve evidence before log rotation, redeployment or destructive remediation unless doing so prolongs active exposure.

Sources include backend structured request logs; authentication/session create, refresh and revoke events; sensitive-record access and authorization decisions; database logs; object-storage access logs; Railway deployment/build/runtime logs; Netlify build logs where relevant; AI/subprocessor request metadata and notices; configuration snapshots, deployment SHAs and migration heads.

For each export record source, collector, collection time/timezone, query/filter, time range, filename/object reference, byte size and SHA-256 digest. Keep an untouched original in a restricted location separate from the affected service; analyze a copy; restrict and log access/transfers; preserve provider case IDs; document missing logs, clock drift, retention limits and collection failures.

Do not claim logs are immutable or tamper-proof merely because they are in PostgreSQL. Hash columns in the audit schema do not prove hash chaining is implemented, verified or protected from privileged alteration. Export evidence to separately controlled storage and record hashes. Never store raw tokens, passwords, keys, full care notes or unnecessary health details in incident-event notes.

## 8. Incident register and timeline

Migration 20261027_08_privacy_delivery defines durable privacy_incidents and privacy_incident_events tables alongside audit, sensitive-access and authorization-decision tables. Table existence is not proof of an operator workflow, API, retention policy, backup, alert or access control.

Every incident record must include: incident ID/status/severity/type/owner; detected and reported times; systems/deployment SHA/provider cases; minimum-necessary affected identity/resource references; data categories/jurisdictions/estimated affected-person count; containment actions and timestamps; evidence references and digests; findings/unknowns/confidence; legal assessment and decision-maker; notification decisions/content/channel/sent time/delivery evidence; recovery verification, root cause, corrective actions and review date.

Append a timeline event for every material action or decision. Correct earlier events with a superseding correction event; do not silently rewrite history.

### Database operator reference

This is a schema-oriented reference, not a ready-to-run incident write. Confirm live schema and permissions first. Use an approved restricted database client. Do not place care content or secrets in notes/metadata.

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name IN ('privacy_incidents', 'privacy_incident_events')
ORDER BY table_name, ordinal_position;

SELECT id, incident_status, severity, detected_at, closed_at,
       incident_type, owner_id, created_at, updated_at
FROM privacy_incidents
ORDER BY detected_at DESC;

SELECT id, incident_id, event_type, occurred_at, actor_id, notes_redacted
FROM privacy_incident_events
WHERE incident_id = :incident_id
ORDER BY occurred_at, id;
```

Do not issue ad hoc UPDATE/DELETE statements against incident evidence or audit history. The repository currently has no verified role-protected incident-management API/console; restricted database operations remain a manual operational dependency until an authenticated operator workflow is implemented and tested.

## 9. Legal, regulatory and contractual assessment

Promptly involve qualified privacy/legal counsel for any suspected personal-data exposure. Assess controller/processor roles, establishment, affected individuals' locations, processing purpose, data categories, contracts and applicable laws.

Assess where relevant:
- Nigeria Data Protection Act 2023 and NDPC breach reporting requirements, including the 72-hour notification requirement where a breach is likely to result in risk to individuals' rights and freedoms, and communication to affected individuals where the high-risk threshold is met.
- FTC Health Breach Notification Rule applicability to SolenOS as a personal health record vendor or related entity, including individual, FTC and media notification thresholds.
- HIPAA only if SolenOS is a covered entity/business associate or otherwise within scope. Health-related data alone does not automatically establish HIPAA coverage.
- Applicable US state laws, other countries' laws, contracts and provider/subprocessor terms.
- Law-enforcement requests and preservation obligations, with counsel review.

For each framework record applicability (yes/no/uncertain), facts relied upon, decision-maker, counsel consulted, trigger/discovery date, deadline calculation, recipient, content, delivery method, completion evidence and non-notification rationale. Internal SEV targets are not legal deadlines. Do not wait for a final forensic report if applicable law requires notice within a fixed period after awareness/discovery; counsel must validate the trigger and deadline.

Official starting points (re-check current law/guidance at incident time):
- FTC Health Breach Notification Rule: https://www.ftc.gov/business-guidance/resources/complying-ftcs-health-breach-notification-rule-0
- Nigeria Data Protection Commission: https://ndpc.gov.ng/
- US HHS HIPAA Breach Notification Rule, only if applicable: https://www.hhs.gov/hipaa/for-professionals/breach-notification/

## 10. Caregiver and regulator notification

Only the Incident Commander with qualified legal/privacy advice may approve the regulatory-reporting decision. Record the decision and rationale. Caregiver notices must be accurate, direct and accessible and include: verified description and timing; categories of information involved without repeating sensitive care details; containment/investigation steps; proportionate actions for the caregiver; known unknowns and next update; verified monitored contact channel.

Do not speculate about an attacker, claim information was not accessed without evidence, promise no harm occurred, or treat a caregiver notice as a substitute for legally required regulator reporting.

### Caregiver notification template

**Subject: Important security notice about your SolenOS information**

We are contacting you about a security incident involving SolenOS.

**What happened:** [Verified facts. Separate confirmed facts from matters still under investigation.]

**When:** [Known/estimated incident period.] We discovered the issue on [date/time and timezone].

**Information involved:** [Only categories confirmed or reasonably believed to be involved. Do not include another person's information.]

**What we have done:** [Containment, access revocation, credential rotation, investigation and safeguards completed.]

**What you should do:** [Specific proportionate actions, or state if no action is currently required.]

**What remains under investigation:** [Material unknowns and expected next update.]

**Questions and support:** [Verified monitored security contact.]

Do not send this template until placeholders contain verified facts and required review is complete.

## 11. Recovery and closure

Before restoring service/closing: confirm the weakness is fixed or path safely disabled; prove compromised sessions/credentials fail; test authorization and cross-family isolation with synthetic identities; verify care records were not altered/lost/misattributed; preserve logs/evidence; confirm provider containment or document residual risk; complete required notices or record counsel-reviewed non-notification; assign corrective-action owners and due dates.

Closure requires root cause or unresolved status, impact, timeline, notification decision, recovery evidence, corrective actions, residual risks and approval. HTTP 200 is not proof an incident is resolved.

## 12. Tabletop simulation

Run before caregiver launch and after material changes. Use synthetic records only.

**Scenario:** A caregiver reports that a document from another family is accessible after sign-in.

1. Responder records report and notifies primary/backup contact.
2. Incident Commander opens provisional SEV-1/SEV-2 incident and records unknowns.
3. Revoke the test session and prove the old token is denied.
4. Test cross-family denial with two synthetic families.
5. Identify route, request IDs, deployment SHA and bounded time window.
6. Export, hash and restrict relevant logs.
7. Check files, summaries, AI context and providers for exposure.
8. Record jurisdictional applicability and notification trigger/deadline analysis.
9. Prepare (do not send) a caregiver notice from verified facts.
10. Restore only after regression checks; record findings and corrective actions.

Mark each step PASS/FAIL/BLOCKED with evidence. A tabletop discussion alone does not prove production containment, durable logging, backup recovery or notification delivery.

## 13. P0 release acceptance gates

| Gate | Evidence required | Status from repository inspection |
| --- | --- | --- |
| Primary and backup contacts | Verified contacts and successful test message | BLOCKED — not configured/verified |
| Session containment | Rehearsal proving old token denied after revocation | PARTIAL — revoke route exists in source; production rehearsal not evidenced |
| Account/device suspension | Verified server-side control for each identity type | BLOCKED — not established |
| Cross-family investigation | Synthetic adversarial test and request/audit trace | BLOCKED — incident simulation not evidenced |
| Evidence preservation | Export, hash, restricted storage and retrieval rehearsal | BLOCKED — sources/schema exist; rehearsal not evidenced |
| Incident register | Restricted operator workflow with durable writes | PARTIAL — migration tables exist; no verified role-protected incident console/API |
| Regulatory decision | Named qualified decision-maker/counsel and jurisdiction assessment | BLOCKED — not verified |
| Caregiver notification | Monitored contact channel and reviewed delivery rehearsal | BLOCKED — not verified |
| Tabletop exercise | Completed evidence-backed simulation | NOT RUN |

**Release decision:** This procedure improves preparedness but does not, by itself, satisfy the P0 release gate. Do not describe SolenOS as incident-response ready until blocked gates are completed and evidenced.
