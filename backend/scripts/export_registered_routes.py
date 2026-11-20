#!/usr/bin/env python3
"""Export the routes actually registered on the FastAPI application.

Run from backend/ after installing the backend package:
    python scripts/export_registered_routes.py --output ../artifacts/registered-routes.json

This is a runtime inventory, not a source-code grep. Consumer and persistence
fields use explicit path/module domain mappings; unresolved entries remain marked
REVIEW_REQUIRED. A domain mapping does not prove the exact frontend callsite,
handler write set, transaction boundary, authorization test or end-to-end evidence.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

try:
    # FastAPI >=0.137 preserves included routers as lazy route-tree nodes.
    # iter_route_contexts expands nested includes and returns effective prefixed paths.
    from fastapi.routing import iter_route_contexts
except ImportError:  # FastAPI <0.137 exposes a flattened app.routes list.
    iter_route_contexts = None

from app.main import PUBLIC_V1_ENDPOINTS, app


CONSUMER_DOMAINS = [
    ("/api/v1/privacy/requests/reviewer", ("internal", "Privacy reviewer workflow", "backend/app/api/privacy_requests.py")),
    ("/api/v1/security-incidents", ("internal", "Security incident operations", "backend/app/api/security_incidents.py")),
    ("/api/v1/assistant/evaluate-production", ("internal", "Production inference acceptance", "backend/scripts/run_production_qwen_evaluation.py")),
    ("/api/v1/assistant", ("frontend", "AI Companion assistance", "src/components/workflows/AICompanion.tsx")),
    ("/api/v1/sessions", ("frontend", "Anonymous session lifecycle", "src/api/client.ts")),
    ("/api/v1/accounts", ("frontend", "Optional account and recovery", "src/api/client.ts")),
    ("/api/v1/caregiver-access", ("frontend", "Caregiver invitations and permissions", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/privacy/requests", ("frontend", "Caregiver privacy requests", "src/app/workspace/settings/page.tsx")),
    ("/api/v1/identity", ("frontend", "Session and device security", "src/app/workspace/settings/page.tsx")),
    ("/api/v1/evidence", ("frontend", "Daily Care and Documents evidence", "src/app/workspace/daily-care/page.tsx; src/app/workspace/documents/page.tsx")),
    ("/api/v1/upload", ("frontend", "Documents upload and download", "src/app/workspace/documents/page.tsx")),
    ("/api/v1/documents", ("frontend", "Documents extraction and review", "src/app/workspace/documents/page.tsx")),
    ("/api/v1/document", ("frontend", "Documents extraction and review", "src/app/workspace/documents/page.tsx")),
    ("/api/v1/capture", ("frontend", "Daily Care capture review", "src/components/workflows/CaptureAnalysisReview.tsx")),
    ("/api/v1/daily", ("frontend", "Daily Care capture and intelligence", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/events", ("frontend", "Daily Care event history", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/insights", ("frontend", "Living Care Record understanding", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/corrections", ("frontend", "Care Record correction history", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/observations", ("frontend", "Care Record observations and trends", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/conversation", ("frontend", "AI Companion conversation and retrieval", "src/components/workflows/AICompanion.tsx")),
    ("/api/v1/companion", ("frontend", "AI Companion care-report review", "src/components/workflows/AICompanion.tsx")),
    ("/api/v1/care-delta", ("frontend", "Persisted LCR change history", "src/components/mvp-workspace/CareChangeHistoryPanel.tsx")),
    ("/api/v1/change", ("frontend", "Change and situation review", "src/components/mvp-workspace/CareChangeHistoryPanel.tsx")),
    ("/api/v1/care-continuity", ("frontend", "Canonical Living Care Record", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/uncertainty", ("frontend", "Care Record gaps and contradictions", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/caregiver-profiles", ("frontend", "Caregiver network profiles", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-roles", ("frontend", "Caregiver roles", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-responsibilities", ("frontend", "Care responsibility assignment", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-observations", ("frontend", "Caregiver observations", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-perspectives", ("frontend", "Caregiver perspectives and conflicts", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-handoffs", ("frontend", "Caregiver responsibility handoffs", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-activities", ("frontend", "Caregiver activity history", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-communications", ("frontend", "Caregiver communications", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/care-network", ("frontend", "Care network state and relationships", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/care-network-state", ("frontend", "Care network state", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/caregiver-contexts", ("frontend", "Caregiver context", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/responsibility-gaps", ("frontend", "Responsibility gaps", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/perspective-conflicts", ("frontend", "Perspective conflict review", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/duplicate-actions", ("frontend", "Duplicate action review", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/multi-caregiver-summary", ("frontend", "Multi-caregiver summary", "src/components/mvp-workspace/CaregiverAccessPanel.tsx")),
    ("/api/v1/appointments", ("frontend", "Appointment records and preparation", "src/app/workspace/appointments/page.tsx; src/components/workflows/AppointmentWorkspace.tsx")),
    ("/api/v1/appointment-workspaces", ("frontend", "Appointment preparation and outcome review", "src/app/workspace/appointments/page.tsx; src/components/workflows/AppointmentWorkspace.tsx")),
    ("/api/v1/handoff-drafts", ("frontend", "Care handoff drafts and sharing", "src/app/workspace/handoffs/page.tsx; src/components/workflows/CareHandoffSharing.tsx")),
    ("/api/v1/handoffs", ("frontend", "Care handoff delivery and acknowledgment", "src/app/workspace/handoffs/page.tsx; src/components/workflows/CareHandoffSharing.tsx")),
    ("/api/v1/open-loops", ("frontend", "Open Loops lifecycle", "src/app/workspace/open-loops/page.tsx")),
    ("/api/v1/reminders", ("frontend", "Care reminders", "src/app/workspace/reminders/page.tsx")),
    ("/api/v1/notifications", ("frontend", "Notifications and push subscriptions", "src/app/workspace/notifications/page.tsx")),
    ("/api/v1/registered-actions", ("frontend", "Approved care action lifecycle", "src/app/workspace/open-loops/page.tsx")),
    ("/api/v1/safety", ("internal", "Safety validation and escalation", "backend/app/api/safety.py")),
    ("/api/v1/causal", ("internal", "Evidence-bounded causal analysis", "backend/app/api/causal.py")),
    ("/api/v1/temporal", ("internal", "Temporal reasoning and provenance", "backend/app/api/v1.py")),
    ("/api/v1/time", ("frontend", "Caregiver time utilities", "src/api/client.ts")),
    ("/api/v1/share-intake", ("frontend", "Care handoff intake", "src/app/workspace/handoffs/page.tsx; src/components/workflows/CareHandoffSharing.tsx")),
    ("/api/v1/care-windows", ("frontend", "Daily Care timing context", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/locations", ("frontend", "Care context location records", "src/app/workspace/daily-care/page.tsx")),
    ("/api/v1/transitions", ("frontend", "Care transition history", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx")),
    ("/api/v1/persons", ("frontend", "Care-recipient identity", "src/api/client.ts")),
    ("/api/v1/caregivers", ("frontend", "Caregiver identity", "src/api/client.ts")),
    ("/api/v1/reasoning-memory", ("intentionally_unused", "Legacy reasoning-memory CRUD route has no production frontend or internal HTTP consumer; LearningEngine uses the service/ORM directly; retain only pending deprecation review", "backend/app/services/learning.py")),
    ("/api/v1/reasoning", ("intentionally_unused", "Reasoning-summary API has no production frontend or internal HTTP consumer; LearningEngine is called directly by backend workflows", "backend/app/services/learning.py")),
    ("/api/v1/learning-events", ("intentionally_unused", "Learning-event listing is used by E2E verification only, not a production caregiver workflow; LearningEngine owns writes", "backend/app/services/learning.py")),
    ("/api/v1/feedback", ("frontend", "Caregiver feedback", "src/components/mvp-workspace/HelpImproveSolenos.tsx")),
    ("/api/v1/parse-time", ("frontend", "Caregiver time parsing", "src/api/client.ts")),
    ("/api/v1/situation", ("frontend", "Progressive care understanding", "src/app/workspace/daily-care/page.tsx; src/components/mvp-workspace/SituationResponsePanel.tsx")),
    ("/api/situation", ("frontend", "Progressive care understanding", "src/app/workspace/daily-care/page.tsx; src/components/mvp-workspace/SituationResponsePanel.tsx")),
    ("/api/lcr/orientation", ("frontend", "Returning-caregiver LCR orientation", "src/components/workflows/AICompanion.tsx")),
]

PERSISTENCE_DOMAINS = [
    ("/api/lcr/orientation", "Read-only CareAssertion/EvidenceObservation current-care projection; no writes"),
    ("/api/v1/timezones", "TimezoneContext records and caregiver time-zone preferences"),
    ("/api/v1/assistant/evaluate-production", "No care-record writes; synthetic-only inference acceptance"),
    ("/api/v1/assistant", "QwenBackgroundJob; QwenModelOutput; QwenSafetyEvaluation; AIOutputLCRProposal where caregiver review is required"),
    ("/api/v1/time", "No persistence; deterministic time utility"),
    ("/api/v1/parse-time", "No persistence; deterministic time parsing utility"),
    ("/api/v1/sessions", "AnonymousSession and identity audit records"),
    ("/api/v1/accounts", "CaregiverAccount and account recovery audit records"),
    ("/api/v1/caregiver-access", "CaregiverAccessGrant; invitation and access audit records"),
    ("/api/v1/privacy/requests", "PrivacyRequest; PrivacyExport; privacy delivery and audit records"),
    ("/api/v1/identity", "AnonymousSession; device and identity audit records"),
    ("/api/v1/security-incidents", "Security incident records and audit events"),
    ("/api/v1/evidence", "Evidence and evidence lineage; linked CareEvent/CareAssertion where applicable"),
    ("/api/v1/upload", "Original Evidence/file metadata and durable document storage"),
    ("/api/v1/documents", "Evidence; CaptureAnalysis; document proposals/reviews and accepted CareAssertion records"),
    ("/api/v1/document", "Evidence; CaptureAnalysis; document proposals/reviews and accepted CareAssertion records"),
    ("/api/v1/daily", "CareEvent; Evidence; CaptureAnalysis; CareDeltaRun; CareAssertion and reconciliation records"),
    ("/api/v1/events", "CareEvent and linked Evidence; downstream assertion/reconciliation records"),
    ("/api/v1/conversation", "ConversationSession; ConversationMessage; review proposals only when explicitly created"),
    ("/api/v1/companion", "AIOutputLCRProposal; AIOutputLCRDecision; CareAssertion; CareDeltaRun and reconciliation records"),
    ("/api/v1/care-continuity", "CareAssertion; EvidenceObservation; CareRecordRelationship; contradictions, questions, snapshots and revisions"),
    ("/api/v1/care-delta", "CareDeltaRun and persisted change/situation/source references"),
    ("/api/v1/uncertainty", "InformationGap; OpenQuestion; Contradiction and resolution history"),
    ("/api/v1/change", "Situation; change assessments and source-linked Delta records"),
    ("/api/v1/caregiver-", "CaregiverProfile; role, responsibility, observation, perspective, activity, communication and handoff records"),
    ("/api/v1/caregivers", "Caregiver identity and care relationship"),
    ("/api/v1/care-network", "CareNetworkRelationship; ResponsibilityGap; PerspectiveConflict; DuplicateAction"),
    ("/api/v1/care-network-state", "CareNetworkRelationship; CaregiverProfile; CaregiverResponsibility; PerspectiveConflict; ResponsibilityGap"),
    ("/api/v1/insights", "Insight and source-linked derived information"),
    ("/api/v1/corrections", "Correction records and append-only correction history"),
    ("/api/v1/observations", "Observation and measurement records with provenance"),
    ("/api/v1/care-windows", "CareWindow records and timing context"),
    ("/api/v1/locations", "Location records and location provenance"),
    ("/api/v1/transitions", "CareTransition records and transition history"),
    ("/api/v1/multi-caregiver-summary", "CaregiverProfile; CaregiverResponsibility; CaregiverPerspective; CareNetworkRelationship"),
    ("/api/v1/share-intake", "Care handoff share tokens, delivery and intake audit records"),
    ("/api/v1/appointments", "Appointment and linked evidence/outcome records"),
    ("/api/v1/appointment-workspaces", "AppointmentWorkspaceRecord; AppointmentWorkspaceRevision; proposals and linked evidence"),
    ("/api/v1/handoff-drafts", "HandoffDraft; immutable revisions; source snapshots and delivery records"),
    ("/api/v1/handoffs", "Care handoff and delivery/acceptance/acknowledgment records"),
    ("/api/v1/open-loops", "OpenLoop; linked actions, dependencies, outcome and verification history"),
    ("/api/v1/reminders", "Reminder; delivery receipts and notification records"),
    ("/api/v1/notifications", "Notification; push subscription and delivery receipt records"),
    ("/api/v1/registered-actions", "RegisteredAction; approval, execution, status and audit history"),
    ("/api/v1/safety", "SafetyAuditRecord; SafetyEvent; Escalation; policy and evaluation records"),
    ("/api/v1/causal", "Causal hypothesis/assessment records and evidence links"),
    ("/api/v1/temporal", "TemporalFact; TemporalRelationship; TemporalContradiction and reference-frame records"),
    ("/api/v1/persons", "Person and care-recipient identity"),
    ("/api/v1/reasoning", "Reasoning memory and derived analysis records"),
    ("/api/v1/learning-events", "LearningEvent and reviewed feedback records"),
    ("/api/v1/feedback", "LearningEvent and feedback records"),
    ("/api/v1/situation", "Situation and source-linked care understanding records"),
    ("/api/situation", "Situation and source-linked care understanding records"),
]

MODULE_CONSUMERS = {
    "app.main": ("internal", "Health and readiness operations", "backend/app/main.py"),
    "app.api.v1": ("frontend", "Core care-record and Daily Care API workflows", "src/app/workspace/daily-care/page.tsx; src/components/mvp-workspace/SituationResponsePanel.tsx"),
    "app.api.accounts": ("frontend", "Optional caregiver account and recovery", "src/api/client.ts"),
    "app.api.anonymous_sessions": ("frontend", "Anonymous session lifecycle", "src/api/client.ts"),
    "app.api.caregiver_access": ("frontend", "Caregiver invitations and permissions", "src/components/mvp-workspace/CaregiverAccessPanel.tsx"),
    "app.api.privacy_requests": ("frontend", "Caregiver privacy requests and reviewer operations", "src/app/workspace/settings/page.tsx; backend/app/api/privacy_requests.py"),
    "app.api.identity_security": ("frontend", "Session and device security settings", "src/app/workspace/settings/page.tsx"),
    "app.api.security_incidents": ("internal", "Security incident operations", "backend/app/api/security_incidents.py"),
    "app.api.caregiver_intelligence": ("frontend", "Caregiver network, responsibilities and perspectives", "src/components/mvp-workspace/CaregiverAccessPanel.tsx"),
    "app.api.safety": ("internal", "Safety validation, escalation and audit", "backend/app/api/safety.py"),
    "app.api.causal": ("internal", "Evidence-bounded causal hypothesis analysis", "backend/app/api/causal.py"),
    "app.api.change": ("frontend", "Progressive understanding and change review", "src/components/mvp-workspace/CareChangeHistoryPanel.tsx"),
    "app.api.collaboration": ("frontend", "AI Companion and collaboration; Community remains disabled", "src/components/workflows/AICompanion.tsx"),
    "app.api.companion_review": ("frontend", "AI Companion care-report review", "src/components/workflows/AICompanion.tsx"),
    "app.api.daily_care_intelligence": ("frontend", "Daily Care intelligence", "src/app/workspace/daily-care/page.tsx"),
    "app.api.capture_review": ("frontend", "Daily Care analysis review", "src/components/workflows/CaptureAnalysisReview.tsx"),
    "app.api.document_extract": ("frontend", "Document upload and extraction", "src/app/workspace/documents/page.tsx"),
    "app.api.document_intelligence": ("frontend", "Document intelligence and review", "src/app/workspace/documents/page.tsx"),
    "app.api.document_lifecycle": ("frontend", "Document lifecycle, versioning and review history", "src/app/workspace/documents/page.tsx"),
    "app.api.handoff_drafts": ("frontend", "Care handoff drafting and sharing", "src/app/workspace/handoffs/page.tsx; src/components/workflows/CareHandoffSharing.tsx"),
    "app.api.handoffs": ("frontend", "Care handoff delivery and acknowledgment", "src/app/workspace/handoffs/page.tsx; src/components/workflows/CareHandoffSharing.tsx"),
    "app.api.open_loops": ("frontend", "Open Loops lifecycle", "src/app/workspace/open-loops/page.tsx"),
    "app.api.registered_actions": ("frontend", "Approved care action lifecycle", "src/app/workspace/open-loops/page.tsx"),
    "app.api.reminders": ("frontend", "Care reminders and delivery", "src/app/workspace/reminders/page.tsx"),
    "app.api.push_notifications": ("frontend", "Notifications and push subscriptions", "src/app/workspace/notifications/page.tsx"),
    "app.api.appointment_workspace": ("frontend", "Appointment preparation and outcome review", "src/app/workspace/appointments/page.tsx; src/components/workflows/AppointmentWorkspace.tsx"),
    "app.api.care_continuity": ("frontend", "Canonical Living Care Record and retrieval", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx"),
    "app.api.care_delta": ("frontend", "Persisted change detection and LCR reconciliation", "src/components/mvp-workspace/CareChangeHistoryPanel.tsx"),
    "app.api.uncertainty": ("frontend", "Care Record gaps, questions and contradictions", "src/components/mvp-workspace/LCRCurrentUnderstanding.tsx"),
}

MODULE_PERSISTENCE = {
    "app.main": "No care-record persistence for health/readiness probes; situation and LCR routes are mapped by path",
    "app.api.v1": "Person; Caregiver; Evidence; CareEvent; Insight; Correction; Observation; Appointment; CareTransition; CareWindow; temporal fact/relationship records",
    "app.api.accounts": "CaregiverAccount and account recovery audit records",
    "app.api.anonymous_sessions": "AnonymousSession and identity audit records",
    "app.api.caregiver_access": "CaregiverAccessGrant; invitation and access audit records",
    "app.api.privacy_requests": "PrivacyRequest; PrivacyExport; privacy delivery and audit records",
    "app.api.identity_security": "AnonymousSession; device and identity audit records",
    "app.api.security_incidents": "Security incident records and audit events",
    "app.api.caregiver_intelligence": "CaregiverProfile; CaregiverRole; CaregiverResponsibility; CaregiverObservation; CaregiverPerspective; CaregiverActivity; CaregiverCommunication; CaregiverContext; CareNetworkRelationship; ResponsibilityGap; PerspectiveConflict; DuplicateAction; CaregiverHandoff",
    "app.api.safety": "SafetyAuditRecord; SafetyEvent; Escalation; policy and evaluation records",
    "app.api.causal": "Causal hypothesis/assessment records and evidence links",
    "app.api.change": "Situation; change assessments and source-linked Delta records",
    "app.api.collaboration": "ConversationSession; ConversationMessage; Qwen job/output and audit records",
    "app.api.companion_review": "AIOutputLCRProposal; AIOutputLCRDecision; CareAssertion; CareDeltaRun and reconciliation records",
    "app.api.daily_care_intelligence": "CareDeltaRun; daily intelligence and linked CareAssertion records",
    "app.api.capture_review": "CaptureAnalysis; CareAssertion; review decisions and reconciliation records",
    "app.api.document_extract": "Evidence and durable document metadata/blob storage",
    "app.api.document_intelligence": "CaptureAnalysis; document assertions/proposals and review decisions",
    "app.api.document_lifecycle": "Document version, review and audit history",
    "app.api.handoff_drafts": "HandoffDraft; immutable revisions; source snapshots and delivery records",
    "app.api.handoffs": "Care handoff and delivery/acceptance/acknowledgment records",
    "app.api.open_loops": "OpenLoop; linked actions, dependencies, outcome and verification history",
    "app.api.registered_actions": "RegisteredAction; approval, execution, status and audit history",
    "app.api.reminders": "Reminder; delivery receipts and notification records",
    "app.api.push_notifications": "Notification; push subscription and delivery receipt records",
    "app.api.appointment_workspace": "AppointmentWorkspaceRecord; AppointmentWorkspaceRevision; proposals and linked evidence",
    "app.api.care_continuity": "CareAssertion; EvidenceObservation; CareRecordRelationship; contradictions, questions, snapshots and revisions",
    "app.api.care_delta": "CareDeltaRun and persisted change/situation/source references",
    "app.api.uncertainty": "InformationGap; OpenQuestion; Contradiction and resolution history",
}

def _module_match(module: str, mappings: dict[str, Any]) -> Any:
    return mappings.get(module)


def _domain_match(path: str, mappings: list[tuple[str, Any]]) -> Any:
    for prefix, value in sorted(mappings, key=lambda item: len(item[0]), reverse=True):
        if path.startswith(prefix):
            return value
    return None


def _authentication_disposition(path: str, method: str) -> str:
    if path in {"/health", "/ready"}:
        return "public health/readiness probe; no care-record access"
    if path == "/api/extract":
        return "server-issued bearer session validated by the document extraction handler"
    if path == "/api/v1/assistant/evaluate-production" and method == "POST":
        return "GitHub Actions bearer token plus live main-branch workflow verification"
    if (
        path == "/api/v1/privacy/requests/reviewer/pending"
        or re.fullmatch(r"/api/v1/privacy/requests/[^/]+/(?:review|fulfil-export|fulfil-correction|fulfil-deletion)", path)
    ):
        return "separate privacy reviewer credential"
    if (path, method) in PUBLIC_V1_ENDPOINTS:
        return "explicit public exception; route-specific credential checks still apply"
    if path == "/api/v1/sessions/anonymous" and method == "POST":
        return "anonymous session bootstrap"
    if path == "/api/v1/share-intake" and method == "POST":
        return "share-token bootstrap"
    if path.startswith("/api/v1/share-intake/") and method == "GET":
        return "share-token read"
    if path.startswith("/api/"):
        return "server-issued bearer session enforced by app middleware"
    return "inspect route dependencies and middleware explicitly"


def _registered_route_contexts():
    if iter_route_contexts is not None:
        yield from iter_route_contexts(app.routes)
        return
    yield from app.routes


def _dependency_names(dependant: Any) -> list[str]:
    """List declared FastAPI dependency callables without pretending middleware is a Depends."""
    names: list[str] = []
    visited: set[int] = set()

    def visit(node: Any) -> None:
        if node is None or id(node) in visited:
            return
        visited.add(id(node))
        call = getattr(node, "call", None)
        if call is not None:
            module = getattr(call, "__module__", type(call).__module__)
            qualified = getattr(call, "__qualname__", getattr(call, "__name__", type(call).__name__))
            label = f"{module}.{qualified}"
            if label not in names:
                names.append(label)
        for child in getattr(node, "dependencies", []) or []:
            visit(child)

    visit(dependant)
    return sorted(names)


def _schema_for_operation(operation: dict[str, Any]) -> dict[str, Any]:
    parameters = []
    for parameter in operation.get("parameters", []):
        parameters.append({
            "name": parameter.get("name"),
            "in": parameter.get("in"),
            "required": bool(parameter.get("required")),
            "schema": parameter.get("schema", {}),
        })
    request_body = operation.get("requestBody", {})
    request_content = request_body.get("content", {})
    request_schema = {
        media_type: details.get("schema", {})
        for media_type, details in request_content.items()
    }
    responses = {}
    for status, response in operation.get("responses", {}).items():
        responses[status] = {
            media_type: details.get("schema", {})
            for media_type, details in response.get("content", {}).items()
        }
    return {
        "operation_id": operation.get("operationId"),
        "summary": operation.get("summary"),
        "parameters": parameters,
        "request_body_required": bool(request_body.get("required")),
        "request_body": request_schema,
        "responses": responses,
        "openapi_security": operation.get("security", []),
    }


def build_inventory() -> dict[str, Any]:
    openapi = app.openapi()
    paths = openapi.get("paths", {})
    entries = []
    duplicates: dict[str, list[str]] = defaultdict(list)

    for route_context in _registered_route_contexts():
        route = getattr(route_context, "route", route_context)
        if not isinstance(route, APIRoute):
            continue
        # RouteContext.path/methods/endpoint include every nested include prefix
        # and dependency context; route.path alone is only the leaf declaration.
        effective_path = getattr(route_context, "path", route.path)
        effective_methods = getattr(route_context, "methods", route.methods)
        effective_endpoint = getattr(route_context, "endpoint", route.endpoint)
        effective_dependant = getattr(route_context, "dependant", None) or route.dependant
        declared_dependencies = _dependency_names(effective_dependant)
        for method in sorted(effective_methods or []):
            method = method.upper()
            operation = paths.get(effective_path, {}).get(method.lower())
            key = f"{method} {effective_path}"
            duplicates[key].append(
                f"{effective_endpoint.__module__}.{effective_endpoint.__qualname__}"
            )
            consumer_domain = _domain_match(effective_path, CONSUMER_DOMAINS) or _module_match(
                effective_endpoint.__module__, MODULE_CONSUMERS
            )
            persistence_domain = _domain_match(effective_path, PERSISTENCE_DOMAINS) or _module_match(
                effective_endpoint.__module__, MODULE_PERSISTENCE
            )
            entries.append({
                "endpoint": effective_path,
                "method": method,
                "router_module": effective_endpoint.__module__,
                "handler": effective_endpoint.__qualname__,
                "route_name": getattr(route_context, "name", route.name),
                "registered": True,
                "include_in_schema": bool(getattr(route_context, "include_in_schema", route.include_in_schema)),
                "included_in_openapi": operation is not None,
                "contract": _schema_for_operation(operation) if operation else None,
                "authentication": {
                    "disposition": _authentication_disposition(effective_path, method),
                    "openapi_security": (operation or {}).get("security", []),
                    "declared_fastapi_dependencies": declared_dependencies,
                    "runtime_middleware": "app middleware is enforced independently of OpenAPI security declarations",
                },
                "persistence": (
                    {
                        "domain_tables": persistence_domain,
                        "resolution": "domain-level table family; exact handler write set and transaction boundary still require verification",
                    }
                    if persistence_domain
                    else "REVIEW_REQUIRED: no persistence domain mapping"
                ),
                "consumer": (
                    {
                        "type": consumer_domain[0],
                        "workflow": consumer_domain[1],
                        "source": consumer_domain[2],
                        "resolution": "domain-prefix or owning-router mapping; exact callsite/handler verification still required",
                    }
                    if consumer_domain
                    else "REVIEW_REQUIRED: no consumer domain mapping"
                ),
                "disposition": (
                    "intentionally_unused"
                    if consumer_domain and consumer_domain[0] == "intentionally_unused"
                    else "frontend_product_workflow"
                    if consumer_domain and consumer_domain[0] == "frontend"
                    else "internal_service_or_operations"
                    if consumer_domain
                    else "REVIEW_REQUIRED"
                ),
                "status": (
                    "intentionally unused; reason documented; pending deprecation review"
                    if consumer_domain and consumer_domain[0] == "intentionally_unused"
                    else "registered; consumer and persistence domain mapped; end-to-end verification pending"
                    if consumer_domain and persistence_domain
                    else "registered; classification gap requires review"
                ),
                "verification": "runtime registration and OpenAPI contract cross-check only; workflow, persistence and authorization tests must be linked separately",
            })

    entries.sort(key=lambda item: (item["endpoint"], item["method"], item["handler"]))
    duplicate_routes = {
        key: handlers for key, handlers in duplicates.items() if len(handlers) > 1
    }
    return {
        "title": "SolenOS registered FastAPI route inventory",
        "source": (
            "runtime app.routes expanded with fastapi.routing.iter_route_contexts "
            "when available, cross-checked against app.openapi()"
        ),
        "route_tree_expansion": (
            "iter_route_contexts" if iter_route_contexts is not None else "legacy_flat_app_routes"
        ),
        "application": app.title,
        "version": app.version,
        "route_count": len(entries),
        "unique_method_path_count": len(duplicates),
        "duplicate_method_paths": duplicate_routes,
        "frontend_product_workflow_count": sum(
            route["disposition"] == "frontend_product_workflow" for route in entries
        ),
        "internal_service_or_operations_count": sum(
            route["disposition"] == "internal_service_or_operations" for route in entries
        ),
        "intentionally_unused_count": sum(
            route["disposition"] == "intentionally_unused" for route in entries
        ),
        "disposition_review_required_count": sum(
            route["disposition"] == "REVIEW_REQUIRED" for route in entries
        ),
        "consumer_review_required_count": sum(
            isinstance(route["consumer"], str) and route["consumer"].startswith("REVIEW_REQUIRED")
            for route in entries
        ),
        "persistence_review_required_count": sum(
            isinstance(route["persistence"], str) and route["persistence"].startswith("REVIEW_REQUIRED")
            for route in entries
        ),
        "registered_but_not_in_openapi_count": sum(
            not route["included_in_openapi"] for route in entries
        ),
        "classification_policy": (
            "Unclassified entries are explicit work items; this export does not "
            "claim a frontend/internal consumer, persistence behavior, authorization "
            "or verification without source and runtime evidence."
        ),
        "routes": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown-output")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory()
    output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_output = Path(args.markdown_output) if args.markdown_output else None
    if markdown_output:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            "# Runtime-registered SolenOS API endpoint inventory",
            "",
            f"Registered route-method entries: {inventory['route_count']}",
            f"Unique method/path pairs: {inventory['unique_method_path_count']}",
            f"Duplicate method/path registrations: {len(inventory['duplicate_method_paths'])}",
            f"Frontend product workflow dispositions: {inventory['frontend_product_workflow_count']}",
            f"Internal service/operations dispositions: {inventory['internal_service_or_operations_count']}",
            f"Intentionally unused routes: {inventory['intentionally_unused_count']}",
            f"Disposition review required: {inventory['disposition_review_required_count']}",
            f"Consumer mapping review required: {inventory['consumer_review_required_count']}",
            f"Persistence mapping review required: {inventory['persistence_review_required_count']}",
            f"Registered but excluded from OpenAPI: {inventory['registered_but_not_in_openapi_count']}",
            "",
            "Generated from FastAPI iter_route_contexts/app.routes and cross-checked against app.openapi(). Consumer and persistence entries are domain-level mappings; they do not prove exact handler write sets or end-to-end verification.",
            "",
            "| Method | Endpoint | Router module / handler | Authentication | Consumer | Consumer source | Persistence | Disposition | Status |",
            "|---|---|---|---|---|---|---|",
        ]
        for route in inventory["routes"]:
            consumer = route["consumer"]
            consumer_label = consumer.get("workflow", "REVIEW_REQUIRED") if isinstance(consumer, dict) else consumer
            consumer_source = consumer.get("source", "") if isinstance(consumer, dict) else ""
            persistence = route["persistence"]
            persistence_label = persistence.get("domain_tables", "REVIEW_REQUIRED") if isinstance(persistence, dict) else persistence
            values = [
                route["method"],
                route["endpoint"],
                route["router_module"] + "." + route["handler"],
                route["authentication"]["disposition"],
                str(consumer_label),
                str(consumer_source),
                str(persistence_label),
                route["disposition"],
                route["status"],
            ]
            rows.append("| " + " | ".join(value.replace("|", "\\|").replace("\n", " ") for value in values) + " |")
        markdown_output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(
        f"Exported {inventory['route_count']} registered route-method entries "
        f"({inventory['unique_method_path_count']} unique method/path pairs) "
        f"to {output}"
    )
    print(
        "Endpoint disposition counts: "
        f"frontend={inventory['frontend_product_workflow_count']} "
        f"internal={inventory['internal_service_or_operations_count']} "
        f"review={inventory['disposition_review_required_count']} "
        f"consumer_review={inventory['consumer_review_required_count']} "
        f"persistence_review={inventory['persistence_review_required_count']} "
        f"not_in_openapi={inventory['registered_but_not_in_openapi_count']}"
    )
    if inventory["duplicate_method_paths"]:
        print("Duplicate method/path registrations:")
        for key, handlers in inventory["duplicate_method_paths"].items():
            print(f"  {key}: {', '.join(handlers)}")


if __name__ == "__main__":
    main()
