# SolenOS processor and subprocessor register

**Last checked:** 2026-11-16  
**Status:** INCOMPLETE — this is an evidence register, not a complete vendor disclosure or legal determination.

Do not infer that a provider is a processor, subprocessor, controller or business associate solely from its product name. Confirm the contracting entity, actual data flow, role, contract and applicable law. Do not publish this internal register as a complete public list until every row and downstream provider is verified.

## Known infrastructure references

| Provider/service | Known purpose | Data potentially processed | Known deployment evidence | Contract / DPA | Processing and support locations | Retention / deletion | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Netlify | Frontend hosting and build/deployment | Browser requests, IP/device metadata, account/workspace data visible to frontend, build artifacts and deployment logs | Production site identified as `solenosai.netlify.app`; a prior audit observed function region `us-east-2` on 2026-11-11 | NOT VERIFIED | Function region was observed; CDN, logs, support access, backups and subprocessors not fully mapped | NOT VERIFIED | PARTIAL |
| Railway application hosting | FastAPI backend hosting | Authenticated API traffic, session metadata, care-record data processed by backend, application/runtime logs | Prior audit observed production service in `sfo`; re-check live configuration | NOT VERIFIED | Service region observed historically; support access, log location and onward processing not fully mapped | NOT VERIFIED | PARTIAL |
| Railway PostgreSQL | Primary relational database | Identity/session records, care history, evidence metadata and uploaded document bytes stored in PostgreSQL | Prior audit observed Railway PostgreSQL and persistent volume; re-check live configuration | NOT VERIFIED | Primary service region and backup/replica locations require current provider evidence | NOT VERIFIED | PARTIAL |
| AI/model runtime (Qwen/Ollama path) | Optional document/companion inference path | May receive caregiver prompts, retrieved care context and/or document-derived text depending on the active route | Source contains Qwen/Ollama integration; actual production endpoint, host/operator and whether any external model provider receives data are NOT VERIFIED | NOT VERIFIED | NOT VERIFIED | Model telemetry, prompt/output retention and host logs NOT VERIFIED | BLOCKED |
| Email, analytics, crash reporting, support, monitoring, object storage and other integrations | To be established from code, provider dashboards and billing/contracts | Unknown until each integration is traced | No complete inventory established by this register | NOT VERIFIED | NOT VERIFIED | NOT VERIFIED | BLOCKED pending discovery |

The Netlify/Railway region observations above are historical evidence from the 2026-11-11 audit, not a guarantee of current settings or complete data residency. They do not establish the location of provider backups, logs, support access, CDN processing or subprocessors.

## Required discovery procedure

Before completing the register:

1. Inspect production environment variables and deployment configuration without copying secret values.
2. Trace every outbound network destination from frontend and backend code, including AI, document extraction, email, telemetry, analytics, crash reporting and support tooling.
3. Inspect provider dashboards, account plans, invoices and service settings for enabled products and regions.
4. Obtain the actual contracting entity, terms, data-processing addendum, international transfer terms and subprocessor list for each service.
5. Record primary data region, replicas, backups, logs, support access, subprocessors and onward-transfer locations separately.
6. Record data categories, processing purpose, whether content or metadata is sent, retention/deletion behavior and incident-notification SLA.
7. Confirm provider security contact and emergency escalation channel with a test that does not disclose real care data.
8. Reconcile the register with public privacy disclosures before inviting additional users.

## Required fields for every provider

- Provider brand and contracting legal entity
- Product/service and enabled features
- SolenOS owner and account identifier (never credentials)
- Controller/processor/subprocessor role assessment and rationale
- Processing purpose and data categories
- Data flow direction and whether content, metadata or both are sent
- Primary processing region, backup region, log region and support-access countries
- Subprocessor list and onward transfer mechanism
- Contract, DPA and applicable terms version/date
- Provider retention, deletion, backup expiry and support-ticket retention
- Security incident notice channel and contractual notice period
- Access-control, encryption and export/deletion evidence
- Last verified date, verifier and unresolved questions

## Release gate

The register remains **INCOMPLETE** until all enabled production providers and downstream subprocessors have been reconciled against source, production configuration and contracts. Unknown rows must remain visibly unknown; do not replace them with assumptions or marketing claims.
