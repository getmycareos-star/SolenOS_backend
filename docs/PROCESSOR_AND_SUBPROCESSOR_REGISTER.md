# SolenOS processor and subprocessor register

**Last checked:** 2026-11-16  
**Status:** INCOMPLETE — this is an evidence register, not a complete vendor disclosure or legal determination.

Do not infer that a provider is a processor, subprocessor, controller or business associate solely from its product name. Confirm the contracting entity, actual data flow, role, contract and applicable law. Do not publish this internal register as a complete public list until every row and downstream provider is verified.

## Known infrastructure references

| Provider/service | Known purpose | Data potentially processed | Known deployment evidence | Contract / DPA | Processing and support locations | Retention / deletion | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Netlify | Frontend hosting and build/deployment | Browser requests, IP/device metadata, account/workspace data visible to frontend, build artifacts and deployment logs | Production site identified as `solenosai.netlify.app`; a prior audit observed function region `us-east-2` on 2026-11-11 | Public DPA is available in the [Netlify Trust Center](https://www.netlify.com/trust-center/); account-specific acceptance, contracting entity and applicable terms are NOT VERIFIED | Netlify's [current public subprocessor list](https://www.netlify.com/trust-center/?itemUid=e3fae2ca-94a9-416b-b577-5c90e382df57) lists providers and locations; this is not a verified map of which subprocessors handled SolenOS data. Function region was observed historically; CDN, logs, support access and backups remain unmapped | Public DPA/subprocessor documents identify terms and providers, but SolenOS account-specific retention/deletion settings remain NOT VERIFIED | PARTIAL |
| Railway application hosting | FastAPI backend hosting | Authenticated API traffic, session metadata, care-record data processed by backend, application/runtime logs | Prior audit observed production service in `sfo`; re-check live configuration | Railway publishes a [DPA](https://railway.com/legal/dpa). Its page says the DPA must be completed through DocuSign and becomes binding upon Railway's execution; SolenOS-specific execution/effective date is NOT VERIFIED | The DPA states primary processing operations are in the US and points to Railway's [authorized subprocessor list](https://trust.railway.com/item/subprocessors). Actual service region, support access and exact downstream providers for this project require account-level verification | The public DPA describes vendor-level security/retention terms; account-specific plan, log retention and deletion settings remain NOT VERIFIED | PARTIAL |
| Railway PostgreSQL | Primary relational database | Identity/session records, care history, evidence metadata and uploaded document bytes stored in PostgreSQL | Prior audit observed Railway PostgreSQL and persistent volume; re-check live configuration | Covered by Railway's published DPA only if the customer agreement/DPA is effective for SolenOS; execution remains NOT VERIFIED | Prior service region observation was `sfo`; Railway's DPA states primary operations are US-based and describes underlying infrastructure and subprocessors. Exact database region, backup locations and per-project configuration remain NOT VERIFIED | Railway's DPA states customer data is backed up daily and restore capabilities are tested at the provider level. This is not evidence of SolenOS's own project backup settings, last successful backup, restore access or tested customer restore | PARTIAL |
| GitHub / GitHub Actions | Source control, CI tests, PostgreSQL migration/recovery verification and production readiness monitoring | Source code, commit/build metadata, CI logs, sanitized synthetic backup/recovery reports and sanitized production endpoint observations; the backup workflow does not upload a production database dump | Public backend repository and Actions workflows are in use; repository/Actions plan and account configuration require owner verification | NOT VERIFIED | GitHub processing, support-access and artifact storage locations require current contract/account evidence | Recovery artifacts retained 30 days; production observation artifacts retained 90 days; other log/source retention must be checked against GitHub settings | PARTIAL |
| AI/model runtime (Qwen/Ollama path) | Optional document/companion inference path | May receive caregiver prompts, retrieved care context and/or document-derived text depending on the active route | Source contains Qwen/Ollama integration; actual production endpoint, host/operator and whether any external model provider receives data are NOT VERIFIED | NOT VERIFIED | NOT VERIFIED | Model telemetry, prompt/output retention and host logs NOT VERIFIED | BLOCKED |
| Email, analytics, crash reporting, support, monitoring, object storage and other integrations | To be established from code, provider dashboards and billing/contracts | Unknown until each integration is traced | No complete inventory established by this register | NOT VERIFIED | NOT VERIFIED | NOT VERIFIED | BLOCKED pending discovery |

The Netlify/Railway region observations above are historical evidence from the 2026-11-11 audit, not a guarantee of current settings or complete data residency. They do not establish the location of provider backups, logs, support access, CDN processing or subprocessors.

## Public provider documents checked on 2026-11-17

- **Netlify:** its Trust Center publishes a DPA and a current subprocessor register with named providers, processing purposes and locations. The list includes infrastructure, analytics, logging and AI providers. Do not assume every listed provider processes SolenOS data; trace the enabled Netlify features and data flows. The published DPA describes subprocessor change notice and objection provisions. SolenOS account-specific DPA acceptance and the exact features enabled remain unverified.
- **Railway:** the published DPA identifies Railway Corporation, describes the customer/processor relationship, points to the live authorized-subprocessor register, states that primary processing operations are in the United States, and describes daily vendor-level backups and ongoing restore testing. The DPA page says the customer completes a DocuSign form and the DPA becomes binding upon Railway's execution. Do not treat the public template or vendor-level backup statements as proof of an executed SolenOS DPA, the current service's region, the project's backup configuration or a successful SolenOS restore.
- **Action:** retrieve the executed/accepted agreement evidence from each account, save the current subprocessor list snapshots with retrieval dates, subscribe to change notices where available, and reconcile enabled features against actual outbound traffic and data categories.


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
