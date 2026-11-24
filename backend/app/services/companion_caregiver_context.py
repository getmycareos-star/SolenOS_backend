"""Build trusted, request-scoped caregiver context for Companion.

Identity and access come only from server-resolved database rows. The model
receives this context as data; it never chooses the actor, recipient, or scope.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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


def build_companion_caregiver_context(
    caregiver,
    access_grant,
    *,
    now: datetime | None = None,
    timezone_conflict: bool = False,
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
    if access_scope != "documents_only" and "view_lcr" not in permissions:
        raise ValueError("The authenticated caregiver is not permitted to view this care record")

    full_name = _clean(getattr(caregiver, "name", None), limit=160)
    preferred_name = _clean(getattr(caregiver, "preferred_name", None), limit=80)
    display_name = preferred_name or full_name
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
        "local_time": local_now.strftime("%I:%M %p").lstrip("0") if local_now else None,
        "greeting_period": _greeting_period(local_now) if local_now else "neutral",
        "locale": _clean(getattr(caregiver, "locale", None), limit=16),
        "date_format": _clean(getattr(caregiver, "date_format", None), limit=24),
        "measurement_system": _clean(getattr(caregiver, "measurement_system", None), limit=24),
        "communication_style": _clean(getattr(caregiver, "communication_style", None), limit=24),
    }
