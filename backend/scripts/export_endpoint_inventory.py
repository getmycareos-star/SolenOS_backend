"""Export the actual FastAPI OpenAPI route table with its owning handlers.

Run from the backend directory:
    python scripts/export_endpoint_inventory.py --output reports/registered-endpoint-inventory.json

The inventory is generated from app.openapi(), not source-file grep. It is a
runtime registration inventory; consumer and persistence ownership remain
explicit review fields rather than guessed from a matching URL.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

from app.main import PUBLIC_V1_ENDPOINTS, app


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _schema_name(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        return value["$ref"].rsplit("/", 1)[-1]
    if "type" in value:
        result = {"type": value["type"]}
        if "items" in value:
            result["items"] = _schema_name(value["items"])
        if "enum" in value:
            result["enum"] = value["enum"]
        return result
    return value


def _auth_disposition(path: str, method: str) -> str:
    if path == "/api/v1/assistant/evaluate-production" and method.lower() == "post":
        return "github-actions-token-and-live-main-workflow-verification"
    if (
        path == "/api/v1/privacy/requests/reviewer/pending"
        or re.fullmatch(
            r"/api/v1/privacy/requests/[^/]+/(?:review|fulfil-export|fulfil-correction|fulfil-deletion)",
            path,
        )
    ):
        return "privacy-reviewer-credential-required"
    if (path, method.upper()) in PUBLIC_V1_ENDPOINTS:
        return "explicit-public-exception"
    if path == "/api/v1/sessions/anonymous" and method.lower() == "post":
        return "anonymous-session-bootstrap"
    if path == "/api/v1/share-intake" and method.lower() == "post":
        return "share-token-bootstrap"
    if path.startswith("/api/v1/share-intake/") and method.lower() == "get":
        return "share-token-read"
    if path.startswith("/api/v1/privacy/requests/reviewer/") or "/reviewer/" in path:
        return "reviewer-credential-required"
    if path.startswith("/api/v1/") or path in {"/api/situation", "/api/lcr/orientation"}:
        return "server-issued-bearer-session-required"
    return "outside-v1-session-middleware; inspect route dependencies"


# Domain-level routing map. These entries identify the intended product or
# internal consumer and the persistence family; route-specific table writes
# still require handler/service review and are never guessed from the URL.
CONSUMER_DOMAINS = [
    ("/api/v1/privacy/requests/reviewer", ("internal", "Privacy reviewer workflow", "backend/app/api/privacy_requests.py")),
    ("/api/v1/security-incidents", ("internal", "Security incident operations", "backend/app/api/security_incidents.py")),
    ("/api/v1/assistant/evaluate-production", ("internal", "Production inference acceptance workflow", "backend/scripts/run_production_qwen_evaluation.py")),
    ("/api/v1/assistant", ("frontend", "AI Companion availability and assistance", "src/components/workflows/AICompanion.tsx")),
    ("/api/v1/sessions", ("frontend", "Anonymous session and identity lifecycle", "src/api/client.ts")),
    ("/api/v1/accounts", ("frontend", "Optional account and recovery settings", "src/api/client.ts")),
    ("/api/v1/caregiver-access", ("frontend", "Caregiver invitations and permissions", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/privacy/requests", ("frontend", "Caregiver privacy requests", "src/app/workspace/settings/page.tsx")),
    ("/api/v1/identity", ("frontend", "Session and device security settings", "src/app/workspace/settings/page.tsx")),
    ("/api/v1/evidence", ("frontend", "Daily Care and Documents evidence capture and retrieval", "src/components/workflows")),
    ("/api/v1/upload", ("frontend", "Documents upload and download", "src/components/workflows/DocumentsWorkspace.tsx")),
    ("/api/v1/documents", ("frontend", "Documents extraction, review and history", "src/components/workflows/DocumentsWorkspace.tsx")),
    ("/api/v1/document", ("frontend", "Documents extraction and review", "src/components/workflows/DocumentsWorkspace.tsx")),
    ("/api/v1/capture", ("frontend", "Daily Care capture review", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/daily", ("frontend", "Daily Care capture and intelligence", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/events", ("frontend", "Daily Care event history and LCR projection", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/insights", ("frontend", "Living Care Record current understanding", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/corrections", ("frontend", "Care Record correction and review history", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/observations", ("frontend", "Care Record observations and trends", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/conversation", ("frontend", "AI Companion conversation and historical retrieval", "src/components/workflows/AICompanion.tsx")),
    ("/api/v1/companion", ("frontend", "AI Companion care-report review", "src/components/workflows/AICompanion.tsx")),
    ("/api/v1/care-delta", ("frontend", "Persisted LCR change history", "src/components/mvp-workspace/CareChangeHistoryPanel.tsx")),
    ("/api/v1/change", ("frontend", "Change and situation review", "src/components/mvp-workspace/CareChangeHistoryPanel.tsx")),
    ("/api/v1/care-continuity", ("frontend", "Canonical Living Care Record and unified retrieval", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/uncertainty", ("frontend", "Care Record gaps, questions and contradictions", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/caregiver-profiles", ("frontend", "Caregiver network profiles", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-roles", ("frontend", "Caregiver roles and participation", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-responsibilities", ("frontend", "Care responsibility assignment and history", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-observations", ("frontend", "Caregiver observations", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-perspectives", ("frontend", "Caregiver perspectives and conflicts", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-handoffs", ("frontend", "Caregiver-to-caregiver responsibility handoffs", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-activities", ("frontend", "Caregiver activity history", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-communications", ("frontend", "Caregiver communications", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/care-network", ("frontend", "Care network state and relationships", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-contexts", ("frontend", "Caregiver context", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/responsibility-gaps", ("frontend", "Responsibility gaps and review", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/perspective-conflicts", ("frontend", "Perspective conflict review", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/duplicate-actions", ("frontend", "Duplicate care action review", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-summary", ("frontend", "Caregiver network summary", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/multi-caregiver-summary", ("frontend", "Multi-caregiver summary", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/care-network-state", ("frontend", "Care network state", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/appointments", ("frontend", "Appointment records", "src/components/workflows/AppointmentWorkspace.tsx")),
    ("/api/v1/appointment-workspaces", ("frontend", "Appointment preparation and outcome review", "src/components/workflows/AppointmentWorkspace.tsx")),
    ("/api/v1/handoff-drafts", ("frontend", "Care handoff draft, approval and sharing", "src/components/workflows/CareHandoffSharing.tsx")),
    ("/api/v1/handoffs", ("frontend", "Care handoff delivery and acknowledgment", "src/components/workflows/CareHandoffSharing.tsx")),
    ("/api/v1/open-loops", ("frontend", "Open Loops lifecycle", "src/components/workflows/OpenLoopsWorkspace.tsx")),
    ("/api/v1/reminders", ("frontend", "Care reminders and completion", "src/components/workflows/RemindersWorkspace.tsx")),
    ("/api/v1/notifications", ("frontend", "Care notifications and push subscriptions", "src/components/workflows/NotificationsWorkspace.tsx")),
    ("/api/v1/registered-actions", ("frontend", "Approved and audited care action lifecycle", "src/components/workflows/OpenLoopsWorkspace.tsx")),
    ("/api/v1/safety", ("internal", "Safety validation and escalation", "backend/app/services/safety.py")),
    ("/api/v1/causal", ("internal", "Evidence-bounded causal hypothesis analysis", "backend/app/services/causal_reasoning.py")),
    ("/api/v1/time", ("frontend", "Caregiver time utilities", "src/api/client.ts")),
    ("/api/v1/share-intake", ("frontend", "Approved care handoff intake", "src/components/workflows/CareHandoffSharing.tsx")),
    ("/api/v1/temporal", ("internal", "Temporal reasoning and provenance", "backend/app/services/temporal_intelligence.py")),
    ("/api/v1/timezones", ("frontend", "Caregiver time-zone context", "src/api/client.ts")),
    ("/api/v1/care-windows", ("frontend", "Daily Care timing and routine context", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/locations", ("frontend", "Care context location records", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/transitions", ("frontend", "Care transition history", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/persons", ("frontend", "Care-recipient identity and record setup", "src/api/client.ts")),
    ("/api/v1/caregivers", ("frontend", "Caregiver identity and record setup", "src/api/client.ts")),
    ("/api/v1/reasoning", ("internal", "Care reasoning and explanation services", "backend/app/services")),
    ("/api/v1/learning-events", ("internal", "Reviewed feedback and learning records", "backend/app/services/improvement_learning.py")),
    ("/api/v1/feedback", ("frontend", "Caregiver feedback submission", "src/components")),
    ("/api/v1/parse-time", ("frontend", "Caregiver time parsing", "src/api/client.ts")),
    ("/api/v1/situation", ("frontend", "Progressive care understanding and situation review", "src/components/workflows")),
    ("/api/situation", ("frontend", "Progressive care understanding and situation review", "src/components/workflows")),
    ("/api/lcr/orientation", ("frontend", "Returning-caregiver Care Record orientation", "src/components/workflows/AICompanion.tsx")),
]

PERSISTENCE_DOMAINS = [
    ("/api/v1/assistant/evaluate-production", "No care-record writes; synthetic-only production inference acceptance probe"),
    ("/api/v1/assistant", "QwenBackgroundJob; QwenModelOutput; QwenSafetyEvaluation; AIOutputLCRProposal where a separate caregiver review is required; runtime status endpoints are read-only"),
    ("/api/v1/time", "No persistence; deterministic time utility"),
    ("/api/v1/parse-time", "No persistence; deterministic time parsing utility"),
    ("/api/v1/sessions", "AnonymousSession; session refresh/revocation and identity audit records"),
    ("/api/v1/accounts", "CaregiverAccount; account recovery and identity audit records"),
    ("/api/v1/caregiver-access", "CaregiverAccessGrant; invitation and access audit records"),
    ("/api/v1/privacy/requests", "PrivacyRequest; PrivacyExport; privacy delivery and audit records"),
    ("/api/v1/identity", "AnonymousSession; device and identity security records; identity audit records"),
    ("/api/v1/security-incidents", "Security incident records and audit events"),
    ("/api/v1/evidence", "Evidence and evidence-lineage records; linked CareEvent/CareAssertion where workflow requires"),
    ("/api/v1/upload", "Original Evidence/file metadata and durable document storage"),
    ("/api/v1/documents", "Evidence; CaptureAnalysis; document proposal/review and accepted CareAssertion records"),
    ("/api/v1/daily", "CareEvent; Evidence; CaptureAnalysis; CareDeltaRun; CareAssertion and reconciliation records"),
    ("/api/v1/events", "CareEvent and linked Evidence; downstream CareAssertion/reconciliation records"),
    ("/api/v1/conversation", "ConversationSession; ConversationMessage; Companion proposals only when explicitly persisted"),
    ("/api/v1/companion", "AIOutputLCRProposal; AIOutputLCRDecision; CareAssertion; CareDeltaRun and reconciliation records"),
    ("/api/v1/care-continuity", "CareAssertion; EvidenceObservation; CareRecordRelationship; contradiction, question, snapshot and revision records"),
    ("/api/v1/care-delta", "CareDeltaRun and persisted change/situation/source references"),
    ("/api/v1/uncertainty", "InformationGap; OpenQuestion; Contradiction and resolution history"),
    ("/api/v1/change", "Situation; change assessments and source-linked Delta records"),
    ("/api/v1/caregiver-", "CaregiverProfile; role, responsibility, observation, perspective, activity, communication and handoff records"),
    ("/api/v1/care-network", "CareNetworkRelationship; ResponsibilityGap; PerspectiveConflict; DuplicateAction"),
    ("/api/v1/appointments", "Appointment and linked evidence/outcome records"),
    ("/api/v1/appointment-workspaces", "AppointmentWorkspaceRecord; AppointmentWorkspaceRevision; proposals and linked evidence"),
    ("/api/v1/handoff-drafts", "HandoffDraft; immutable revisions; source snapshots and delivery records"),
    ("/api/v1/handoffs", "Care handoff and delivery/acceptance/acknowledgment records"),
    ("/api/v1/open-loops", "OpenLoop; linked actions, dependencies, outcome and verification history"),
    ("/api/v1/reminders", "Reminder; reminder delivery receipts and notification records"),
    ("/api/v1/notifications", "Notification; push subscription and delivery receipt records"),
    ("/api/v1/registered-actions", "RegisteredAction; approval, execution, status and audit history"),
    ("/api/v1/safety", "SafetyAuditRecord; SafetyEvent; Escalation; policy and evaluation records"),
    ("/api/v1/causal", "Causal hypothesis/assessment records and evidence links"),
    ("/api/v1/temporal", "TemporalFact; TemporalRelationship; TemporalContradiction and reference-frame records"),
    ("/api/v1/persons", "Person and care-recipient identity"),
    ("/api/v1/caregivers", "Caregiver identity and care relationship"),
    ("/api/v1/insights", "Insight and source-linked derived information"),
    ("/api/v1/corrections", "Correction records and append-only correction history"),
    ("/api/v1/observations", "Observation and measurement records with provenance"),
    ("/api/v1/care-windows", "CareWindow records and timing context"),
    ("/api/v1/locations", "Location records and location provenance"),
    ("/api/v1/transitions", "CareTransition records and transition history"),
    ("/api/v1/multi-caregiver-summary", "CaregiverProfile; CaregiverResponsibility; CaregiverPerspective; CareNetworkRelationship"),
    ("/api/v1/share-intake", "Care handoff share tokens, delivery and intake audit records"),
    ("/api/v1/reasoning", "Reasoning memory and derived analysis records"),
    ("/api/v1/learning-events", "LearningEvent and reviewed feedback records"),
    ("/api/v1/feedback", "LearningEvent and feedback records"),
    ("/api/v1/situation", "Situation and source-linked care understanding records"),
    ("/api/situation", "Situation and source-linked care understanding records"),
]

def _domain_match(path: str, mappings: list[tuple[str, Any]]) -> Any:
    for prefix, value in sorted(mappings, key=lambda item: len(item[0]), reverse=True):
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix):
            return value
    return None


def build_inventory() -> dict[str, Any]:
    openapi = app.openapi()
    route_index: dict[tuple[str, str], list[APIRoute]] = defaultdict(list)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method.lower() in HTTP_METHODS:
                route_index[(route.path, method.lower())].append(route)

    endpoints: list[dict[str, Any]] = []
    for path, operations in sorted(openapi.get("paths", {}).items()):
        for method, operation in sorted(operations.items()):
            method_lower = method.lower()
            if method_lower not in HTTP_METHODS:
                continue
            matches = route_index.get((path, method_lower), [])
            owners = [
                {
                    "module": route.endpoint.__module__,
                    "handler": route.endpoint.__name__,
                    "route_name": route.name,
                }
                for route in matches
            ]
            owner_resolution = (
                "resolved-from-registered-APIRoute"
                if len(matches) == 1
                else "multiple-registered-APIRoute-matches"
                if len(matches) > 1
                else "openapi-operation-only; FastAPI may defer included-router expansion"
            )
            if not owners:
                owners = [{
                    "module": None,
                    "handler": operation.get("operationId"),
                    "router_tags": operation.get("tags", []),
                }]
            parameters = []
            for parameter in operation.get("parameters", []):
                parameters.append({
                    "name": parameter.get("name"),
                    "in": parameter.get("in"),
                    "required": parameter.get("required", False),
                    "schema": _schema_name(parameter.get("schema", {})),
                })
            request_body = operation.get("requestBody", {})
            request_content = request_body.get("content", {})
            request_schemas = {
                content_type: _schema_name(spec.get("schema", {}))
                for content_type, spec in request_content.items()
            }
            response_schemas = {}
            for status, response in operation.get("responses", {}).items():
                response_schemas[status] = {
                    content_type: _schema_name(spec.get("schema", {}))
                    for content_type, spec in response.get("content", {}).items()
                }
            endpoints.append({
                "method": method.upper(),
                "path": path,
                "operation_id": operation.get("operationId"),
                "summary": operation.get("summary"),
                "tags": operation.get("tags", []),
                "owners": owners,
                "owner_resolution": owner_resolution,
                "authentication": _auth_disposition(path, method),
                "parameters": parameters,
                "request_body_required": request_body.get("required", False),
                "request_schemas": request_schemas,
                "response_schemas": response_schemas,
                "consumer": (
                    {
                        "type": _domain_match(path, CONSUMER_DOMAINS)[0],
                        "workflow": _domain_match(path, CONSUMER_DOMAINS)[1],
                        "source": _domain_match(path, CONSUMER_DOMAINS)[2],
                        "resolution": "domain-prefix mapping; exact callsite/handler verification still required",
                    }
                    if _domain_match(path, CONSUMER_DOMAINS)
                    else "REVIEW_REQUIRED: no consumer domain mapping"
                ),
                "persistence": (
                    {
                        "domain_tables": _domain_match(path, PERSISTENCE_DOMAINS),
                        "resolution": "domain-level table family; exact handler write set and transaction boundary still require verification",
                    }
                    if _domain_match(path, PERSISTENCE_DOMAINS)
                    else "REVIEW_REQUIRED: no persistence domain mapping"
                ),
                "integration_status": (
                    "registered-handler-match"
                    if len(matches) == 1
                    else "owner-resolution-required"
                    if not matches
                    else "duplicate-handler-match"
                ),
                "verification": "REVIEW_REQUIRED: attach contract, persistence and authorization test evidence",
            })

    community_routes = [
        endpoint for endpoint in endpoints
        if endpoint["path"].lower().startswith("/api/v1/community")
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "FastAPI app.openapi() and registered APIRoute handlers",
        "application": app.title,
        "version": app.version,
        "endpoint_count": len(endpoints),
        "registered_handler_match_count": sum(
            endpoint["integration_status"] == "registered-handler-match"
            for endpoint in endpoints
        ),
        "owner_resolution_required_count": sum(
            endpoint["integration_status"] == "owner-resolution-required"
            for endpoint in endpoints
        ),
        "duplicate_handler_match_count": sum(
            endpoint["integration_status"] == "duplicate-handler-match"
            for endpoint in endpoints
        ),
        "community_route_count": len(community_routes),
        "endpoints": endpoints,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/registered-endpoint-inventory.json")
    parser.add_argument("--markdown-output", default="reports/registered-endpoint-inventory.md")
    args = parser.parse_args()
    output = Path(args.output)
    markdown_output = Path(args.markdown_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory()
    output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_rows = [
        "# Runtime-registered SolenOS API endpoint inventory",
        "",
        f"Generated: {inventory['generated_at']}",
        "",
        f"- Registered OpenAPI operations: **{inventory['endpoint_count']}**",
        f"- Direct handler matches: **{inventory['registered_handler_match_count']}**",
        f"- Handler-owner resolution required: **{inventory['owner_resolution_required_count']}**",
        f"- Duplicate handler matches: **{inventory['duplicate_handler_match_count']}**",
        f"- Community routes registered: **{inventory['community_route_count']}**",
        "",
        "Generated from the FastAPI application object through app.openapi(). Consumer and persistence entries are domain-level mappings; exact handler write sets, transaction boundaries, frontend callsites and test evidence still require review.",
        "",
        "| Method | Endpoint | Handler / router | Authentication | Consumer | Persistence domain | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for endpoint in inventory["endpoints"]:
        owner = endpoint["owners"][0] if endpoint["owners"] else {}
        handler = owner.get("module") or owner.get("router_tags") or "unresolved"
        handler = str(handler).replace("|", "\\|")
        consumer = endpoint["consumer"]
        consumer_label = consumer.get("workflow", "REVIEW_REQUIRED") if isinstance(consumer, dict) else consumer
        persistence = endpoint["persistence"]
        persistence_label = persistence.get("domain_tables", "REVIEW_REQUIRED") if isinstance(persistence, dict) else persistence
        values = [
            endpoint["method"],
            endpoint["path"],
            handler,
            endpoint["authentication"],
            str(consumer_label),
            str(persistence_label),
            endpoint["integration_status"],
        ]
        markdown_rows.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", " ") for value in values) + " |")
    markdown_output.write_text("\n".join(markdown_rows) + "\n", encoding="utf-8")
    print(
        "REGISTERED_ENDPOINT_INVENTORY "
        f"count={inventory['endpoint_count']} "
        f"matched={inventory['registered_handler_match_count']} "
        f"owner_review={inventory['owner_resolution_required_count']} "
        f"duplicates={inventory['duplicate_handler_match_count']} "
        f"community={inventory['community_route_count']} "
        f"output={output} markdown={markdown_output}"
    )
    if inventory["duplicate_handler_match_count"]:
        return 1
    if inventory["community_route_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
