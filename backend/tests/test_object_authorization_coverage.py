"""Static coverage guard for object-ID and person-scoped authorization.

Behavioral tests cover cross-account appointment, evidence-download, and conversation
session cases. This test additionally guards the registered route handlers against
removing the explicit access-check call/dependency during future edits.
"""
import inspect
import re

from fastapi.routing import APIRoute

from app.api.actions import router as actions_router
from app.api.authentication import router as authentication_router
from app.api.capture import router as capture_router
from app.api.caregiver_intelligence import router as caregiver_intelligence_router
from app.api.causal import router as causal_router
from app.api.change import router as change_router
from app.api.collaboration import community_router, conversation_router, professional_router, team_router
from app.api.safety import router as safety_router
from app.api.uncertainty import router as uncertainty_router
from app.api.v1 import router as v1_router


ROUTERS = (
    authentication_router,
    capture_router,
    v1_router,
    actions_router,
    uncertainty_router,
    change_router,
    caregiver_intelligence_router,
    safety_router,
    causal_router,
    community_router,
    conversation_router,
    professional_router,
    team_router,
)

# A route dependency may implement the authorization check even when the endpoint
# body does not. Search source for explicit grant/link/staff enforcement markers.
AUTHORIZATION_MARKERS = (
    "assert_person_access",
    "require_person_access",
    "assert_account_is_caregiver",
    "assert_account_is_caregiver_profile",
    "require_staff",
    "AccountPersonAccess",
    "CaregiverAccountLink",
    "CaregiverProfileAccountLink",
    "invited_by_account_id",
    "accepted_by_account_id",
    "account.id",
    "account_id",
    "token_digest",
)


def _dependency_source(dependant, seen=None):
    seen = seen or set()
    parts = []
    if id(dependant) in seen:
        return ""
    seen.add(id(dependant))
    call = dependant.call
    if call is not None:
        try:
            parts.append(inspect.getsource(call))
        except (OSError, TypeError):
            parts.append(getattr(call, "__qualname__", ""))
    for child in dependant.dependencies:
        parts.append(_dependency_source(child, seen))
    return "\n".join(parts)


def test_sensitive_object_id_routes_have_explicit_scope_enforcement():
    missing = []
    visited = set()
    for router in ROUTERS:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            identity = (route.path, tuple(sorted(route.methods or set())))
            if identity in visited:
                continue
            visited.add(identity)
            path_params = re.findall(r"{([a-zA-Z_][a-zA-Z0-9_]*)}", route.path)
            if not path_params:
                continue
            source = _dependency_source(route.dependant)
            if not any(marker in source for marker in AUTHORIZATION_MARKERS):
                missing.append(f"{','.join(sorted(route.methods or set()))} {route.path}")

    assert not missing, (
        "Object-ID/path-parameter routes without a recognizable explicit access "
        "enforcement marker: " + ", ".join(sorted(missing))
    )
