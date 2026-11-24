"""Build trusted, request-scoped caregiver context for Companion.

Identity and access come only from server-resolved database rows. The model
receives this context as data; it never chooses the actor, recipient, or scope.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.caregiver_permissions import CAREGIVER_PERMISSION_KEYS, caregiver_has_permission


def _clean(value, *, limit: int = 120) -> str | None:
    if value is None:
        return None
    result = " ".join(str(value).split()).strip()
    return result[:limit] or None


def _greeting_period(local_now: datetime) -> str:
    hour = local_now.hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "neutral"


def _enum_value(value):
    return getattr(value, "value", value)


def _iso_value(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _clean_list(values, *, limit: int = 8, item_limit: int = 240) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [clean for value in values[:limit] if (clean := _clean(value, limit=item_limit))]


def _responsibility_context(row) -> dict:
    return {
        "responsibility_type": _clean(getattr(row, "responsibility_type", None), limit=100),
        "scope": _clean(getattr(row, "scope", None), limit=400),
        "status": _enum_value(getattr(row, "status", None)),
        "acceptance_status": _enum_value(getattr(row, "acceptance_status", None)),
        "effective_start": _iso_value(getattr(row, "effective_start", None)),
        "effective_end": _iso_value(getattr(row, "effective_end", None)),
        "assignment_source": _clean(getattr(row, "assignment_source", None), limit=80),
        "is_joint": bool(getattr(row, "is_joint", False)),
        "schedule_pattern": _clean(getattr(row, "schedule_pattern", None), limit=160),
        "provenance": _clean(getattr(row, "provenance", None), limit=320),
    }


def _role_context(row) -> dict:
    return {
        "role_type": _enum_value(getattr(row, "role_type", None)),
        "custom_role_name": _clean(getattr(row, "custom_role_name", None), limit=100),
        "scope": _clean(getattr(row, "scope", None), limit=400),
        "effective_start": _iso_value(getattr(row, "effective_start", None)),
        "effective_end": _iso_value(getattr(row, "effective_end", None)),
        "assignment_source": _clean(getattr(row, "assignment_source", None), limit=80),
        "is_current": bool(getattr(row, "is_current", False)),
    }


def _open_loop_context(row) -> dict:
    return {
        "id": _clean(getattr(row, "id", None), limit=80),
        "loop_type": _clean(getattr(row, "loop_type", None), limit=40),
        "title": _clean(getattr(row, "title", None), limit=180),
        "description": _clean(getattr(row, "description", None), limit=800),
        "status": _clean(getattr(row, "status", None), limit=40),
        "outcome_status": _clean(getattr(row, "outcome_status", None), limit=32),
        "owner_role": _clean(getattr(row, "owner_role", None), limit=80),
        "owner_label": _clean(getattr(row, "owner_label", None), limit=120),
        "uncertainty": _clean(getattr(row, "uncertainty", None), limit=40),
        "missing_information": _clean_list(getattr(row, "missing_information", None), limit=8),
        "request_text": _clean(getattr(row, "request_text", None), limit=600),
        "recipient_label": _clean(getattr(row, "recipient_label", None), limit=120),
        "deadline_at": _iso_value(getattr(row, "deadline_at", None)),
        "deadline_timezone": _clean(getattr(row, "deadline_timezone", None), limit=80),
        "deadline_source": _clean(getattr(row, "deadline_source", None), limit=40),
        "priority": _clean(getattr(row, "priority", None), limit=16),
        "priority_reasons": _clean_list(getattr(row, "priority_reasons", None), limit=5),
        "deferred_reason": _clean(getattr(row, "deferred_reason", None), limit=400),
        "review_at": _iso_value(getattr(row, "review_at", None)),
        "consequence_of_delay": _clean(getattr(row, "consequence_of_delay", None), limit=40),
        "verification_rule": _clean(getattr(row, "verification_rule", None), limit=400),
    }


def build_companion_caregiver_context(
    caregiver,
    access_grant,
    *,
    now: datetime | None = None,
    timezone_conflict: bool = False,
    caregiver_profile=None,
    authenticated_account_id: str | None = None,
    active_roles=None,
    active_roles_truncated: bool = False,
    active_responsibilities=None,
    assigned_open_loops=None,
    responsibilities_truncated: bool = False,
    open_loops_truncated: bool = False,
) -> dict:
    """Return a JSON-safe context for one authenticated caregiver/person pair.

    A database default of UTC is not treated as a caregiver-confirmed local
    timezone. The caregiver must have explicitly configured it. Conflicting
    legacy notification settings suppress local-time claims until reconciled.
    """
    caregiver_id = str(getattr(caregiver, "id", "") or "")
    person_id = str(getattr(caregiver, "person_id", "") or "")
    if not caregiver_id or not person_id:
        raise ValueError("Authenticated caregiver identity is incomplete")
    if (
        access_grant is None
        or str(getattr(access_grant, "caregiver_id", "")) != caregiver_id
        or str(getattr(access_grant, "person_id", "")) != person_id
        or getattr(access_grant, "status", None) != "active"
        or getattr(access_grant, "revoked_at", None) is not None
    ):
        raise ValueError("An active caregiver grant for this care record is required")

    permissions = getattr(access_grant, "permissions", None)
    permissions = sorted({
        str(value) for value in permissions
        if isinstance(value, str) and value.strip()
    }) if isinstance(permissions, (list, tuple, set)) else []
    access_scope = _clean(getattr(access_grant, "access_scope", None), limit=40) or "unknown"
    # Keep Companion context construction aligned with the canonical permission
    # evaluator. In particular, a member manager's explicit grant is valid even
    # when its permission list is intentionally narrower; documents-only grants
    # must never receive care-record context.
    if not caregiver_has_permission(access_grant, "view_lcr"):
        raise ValueError("The authenticated caregiver is not permitted to view this care record")
    # The model receives effective capabilities, not merely the stored list:
    # can_manage_members grants every supported full-record permission through
    # the canonical evaluator, while documents-only remains explicitly bounded.
    if access_scope == "documents_only":
        permissions = [
            permission for permission in sorted(CAREGIVER_PERMISSION_KEYS)
            if caregiver_has_permission(access_grant, permission)
        ]
    elif bool(getattr(access_grant, "can_manage_members", False)):
        permissions = sorted(CAREGIVER_PERMISSION_KEYS)
    else:
        permissions = [
            permission for permission in permissions
            if caregiver_has_permission(access_grant, permission)
        ]

    full_name = _clean(getattr(caregiver, "name", None), limit=160)
    preferred_name = _clean(getattr(caregiver, "preferred_name", None), limit=80)
    first_name = full_name.split()[0] if full_name else None
    if first_name and first_name.casefold() in {"family", "caregiver", "primary", "unknown"}:
        first_name = None
    display_name = preferred_name or first_name
    timezone_name = _clean(getattr(caregiver, "timezone", None), limit=64)
    timezone_configured = bool(getattr(caregiver, "timezone_configured", False))
    timezone_status = "not_configured"
    local_now = None
    if timezone_conflict:
        timezone_status = "conflict"
    elif timezone_configured and timezone_name:
        try:
            zone = ZoneInfo(timezone_name)
            instant = now or datetime.now(timezone.utc)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            local_now = instant.astimezone(zone)
            timezone_status = "configured"
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            timezone_status = "invalid"

    return {
        "caregiver_id": caregiver_id,
        # Internal audit identity only. The prompt identity envelope deliberately
        # excludes account/session identifiers and never asks Qwen to authorize.
        "authenticated_account_id": _clean(authenticated_account_id, limit=80),
        "care_recipient_id": person_id,
        "full_name": full_name,
        "preferred_name": preferred_name,
        "display_name": display_name,
        "relationship": _clean(getattr(caregiver, "relationship", None), limit=80),
        "caregiver_role": _clean(getattr(access_grant, "role", None), limit=32),
        "access_scope": access_scope,
        "permissions": permissions,
        "timezone_name": timezone_name if timezone_status == "configured" else None,
        "timezone_status": timezone_status,
        "local_datetime": local_now.isoformat() if local_now else None,
        "local_date": local_now.date().isoformat() if local_now else None,
        "local_day_of_week": local_now.strftime("%A") if local_now else None,
        "local_time": local_now.strftime("%I:%M %p").lstrip("0") if local_now else None,
        "local_utc_offset": local_now.strftime("%z") if local_now else None,
        "greeting_period": _greeting_period(local_now) if local_now else "neutral",
        "locale": _clean(getattr(caregiver, "locale", None), limit=16),
        "date_format": _clean(getattr(caregiver, "date_format", None), limit=24),
        "measurement_system": _clean(getattr(caregiver, "measurement_system", None), limit=24),
        "communication_style": _clean(getattr(caregiver, "communication_style", None), limit=24),
        "caregiver_profile": {
            "participation_category": _enum_value(getattr(caregiver_profile, "participation_category", None)),
            "involvement_status": _enum_value(getattr(caregiver_profile, "involvement_status", None)),
            "availability_status": _enum_value(getattr(caregiver_profile, "availability_status", None)),
            "is_primary_designated": bool(getattr(caregiver_profile, "is_primary_designated", False)),
        "primary_designation_source": _clean(getattr(caregiver_profile, "primary_designation_source", None), limit=80),
        "proximity_category": _enum_value(getattr(caregiver_profile, "proximity_category", None)),
        } if caregiver_profile is not None else None,
        "active_roles": [_role_context(row) for row in (active_roles or [])[:10]],
        "active_roles_status": "available" if active_roles else "no_rows_returned",
        "active_roles_truncated": bool(active_roles_truncated),
        "active_responsibilities": [
            _responsibility_context(row) for row in (active_responsibilities or [])[:10]
        ],
        "active_responsibilities_status": (
            "profile_not_linked" if caregiver_profile is None
            else "available" if active_responsibilities
            else "no_rows_returned"
        ),
        "active_responsibilities_truncated": bool(responsibilities_truncated),
        "assigned_open_loops": [_open_loop_context(row) for row in (assigned_open_loops or [])[:8]],
        "assigned_open_loops_status": "available" if assigned_open_loops else "no_rows_returned",
        "assigned_open_loops_truncated": bool(open_loops_truncated),
    }


def build_companion_prompt_identity_context(
    caregiver_context: dict,
    care_recipient_name: str | None,
    *,
    greeting_only: bool = False,
    identity_question: bool = False,
) -> dict:
    """Build the minimal, server-authenticated identity envelope sent to Companion.

    Product-identity questions receive no caregiver or care-recipient identity.
    Greetings may use the authenticated caregiver's preferred name, but never
    disclose the care recipient's name, relationship-to-recipient, or care roles.
    Care questions receive the verified caregiver/recipient pairing and the
    caregiver's actual access scope; the model is informed, not authorised.
    """
    if identity_question:
        return {"identity_context_status": "omitted_for_product_identity_question"}

    context = caregiver_context if isinstance(caregiver_context, dict) else {}
    result = {
        "identity_context_status": "authenticated",
        "caregiver_display_name": context.get("display_name"),
        "caregiver_relationship": context.get("relationship"),
        "caregiver_role": context.get("caregiver_role"),
        "access_scope": context.get("access_scope"),
        "permitted_capabilities": list(context.get("permissions") or []),
        "timezone_status": context.get("timezone_status", "not_configured"),
        "timezone_name": context.get("timezone_name"),
        "local_datetime": context.get("local_datetime"),
        "local_date": context.get("local_date"),
        "local_day_of_week": context.get("local_day_of_week"),
        "local_time": context.get("local_time"),
        "local_utc_offset": context.get("local_utc_offset"),
        "greeting_period": context.get("greeting_period", "neutral"),
        "locale": context.get("locale"),
        "date_format": context.get("date_format"),
        "measurement_system": context.get("measurement_system"),
        "communication_style": context.get("communication_style"),
    }
    if greeting_only:
        result["identity_context_status"] = "authenticated_caregiver_only"
        result["care_recipient_name"] = None
        result["caregiver_relationship"] = None
        result["caregiver_role"] = None
        result["access_scope"] = None
        result["permitted_capabilities"] = []
        return result

    result["care_recipient_name"] = _clean(care_recipient_name, limit=160)
    result["caregiver_profile"] = context.get("caregiver_profile")
    result["active_roles"] = context.get("active_roles", [])
    result["active_roles_status"] = context.get("active_roles_status")
    result["active_roles_truncated"] = bool(context.get("active_roles_truncated", False))
    result["active_responsibilities"] = context.get("active_responsibilities", [])
    result["active_responsibilities_status"] = context.get("active_responsibilities_status")
    result["active_responsibilities_truncated"] = bool(context.get("active_responsibilities_truncated", False))
    result["assigned_open_loops"] = context.get("assigned_open_loops", [])
    result["assigned_open_loops_status"] = context.get("assigned_open_loops_status")
    result["assigned_open_loops_truncated"] = bool(context.get("assigned_open_loops_truncated", False))
    return result
