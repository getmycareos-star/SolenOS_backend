"""Authorization Engine for SolenOS.

Implements multi-layered authorization:
- RBAC (Role-Based Access Control)
- ABAC (Attribute-Based Access Control)  
- Relationship-Based Access Control (ReBAC)
- Consent-based access
- Care-context membership
- Data classification enforcement
- Purpose-based access
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Set, Any, Tuple
from enum import Enum
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy.orm import Session

from app.core.data_classification import DataClassification, get_policy
from app.core.identity_enums import (
    AccountType, CaregiverRole, ProfessionalRole, ProfessionalVerificationStatus,
    SessionType,
)
from app.core.sharing_enums import (
    ShareCategory, ShareScope, ShareGrantStatus, ShareRelationship,
)
from app.models.identity import (
    Account, Person, AccountPersonLink, ProfessionalProfile, ProfessionalRoleLink,
    StaffProfile, Session, Device
)
from app.models.care_sharing import CareShareGrant, CareShareAccessLog
from app.models.caregiver import Caregiver


class AuthorizationDecision(str, Enum):
    """Authorization decision."""
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not_applicable"


class AuthorizationContext(str, Enum):
    """Context for authorization decision."""
    API_READ = "api_read"
    API_WRITE = "api_write"
    API_DELETE = "api_delete"
    DOCUMENT_ACCESS = "document_access"
    DOCUMENT_DOWNLOAD = "document_download"
    DOCUMENT_UPLOAD = "document_upload"
    EXPORT = "export"
    SHARE_CREATE = "share_create"
    SHARE_REVOKE = "share_revoke"
    CONSENT_GRANT = "consent_grant"
    CONSENT_WITHDRAW = "consent_withdraw"
    PROFESSIONAL_VERIFY = "professional_verify"
    ADMIN_ACCESS = "admin_access"
    AI_PROCESSING = "ai_processing"
    SEARCH = "search"
    GRAPH_TRAVERSAL = "graph_traversal"


@dataclass
class AuthorizationRequest:
    """Request for authorization decision."""
    actor_account_id: str
    actor_session_id: str
    action: AuthorizationContext
    resource_type: str
    resource_id: str
    person_id: Optional[str] = None  # Care context (care recipient)
    data_classification: DataClassification = DataClassification.PRIVATE_CARE
    purpose: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthorizationResult:
    """Result of authorization decision."""
    decision: AuthorizationDecision
    reason: str
    policy_version: str = "1.0"
    matched_policies: List[str] = field(default_factory=list)
    required_consent: Optional[str] = None
    required_role: Optional[str] = None
    audit_data: Dict[str, Any] = field(default_factory=dict)


class Policy:
    """Authorization policy."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        raise NotImplementedError


class DenyByDefaultPolicy(Policy):
    """Base policy - deny by default."""
    
    def __init__(self):
        super().__init__("deny_by_default", "Deny all access by default")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        return AuthorizationResult(
            decision=AuthorizationDecision.DENY,
            reason="Deny by default - no matching allow policy",
            matched_policies=[self.name],
        )


class AuthenticationPolicy(Policy):
    """Require authentication for sensitive operations."""
    
    def __init__(self):
        super().__init__("require_authentication", "Require valid authentication")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        policy = get_policy(request.data_classification)
        
        if not policy.requires_authentication:
            return AuthorizationResult(
                decision=AuthorizationDecision.NOT_APPLICABLE,
                reason="Authentication not required for this classification",
            )
        
        # Check if actor has valid session
        if not request.actor_account_id or request.actor_account_id == "":
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason="Authentication required",
                required_role="authenticated_user",
            )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.NOT_APPLICABLE,
            reason="Authentication check passed",
        )


class AccountTypePolicy(Policy):
    """Authorize based on account type."""
    
    def __init__(self):
        super().__init__("account_type", "Authorize based on account type")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        # This would be implemented with DB lookup in real usage
        return AuthorizationResult(
            decision=AuthorizationDecision.NOT_APPLICABLE,
            reason="Account type policy requires DB context",
        )


class CareContextMembershipPolicy(Policy):
    """Authorize based on care context membership."""
    
    def __init__(self):
        super().__init__("care_context_membership", "Authorize based on care context membership")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        if not request.person_id:
            return AuthorizationResult(
                decision=AuthorizationDecision.NOT_APPLICABLE,
                reason="No care context specified",
            )
        
        # Check if person_id is in authorized persons
        if request.person_id not in (request.metadata.get("authorized_person_ids", [])):
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason="Not a member of this care context",
                required_role="care_context_member",
                audit_data={"required_person_id": request.person_id},
            )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason="Care context membership verified",
            matched_policies=[self.name],
        )


class CaregiverRolePolicy(Policy):
    """Authorize based on caregiver role in care context."""
    
    ROLE_HIERARCHY = {
        CaregiverRole.PRIMARY: 4,
        CaregiverRole.SECONDARY: 3,
        CaregiverRole.DELEGATE: 2,
        CaregiverRole.OBSERVER: 1,
        CaregiverRole.EMERGENCY: 5,  # Break-glass
    }
    
    # Minimum role required for each action/category
    MIN_ROLE_FOR_ACTION = {
        (AuthorizationContext.API_READ, ShareCategory.DAILY_CARE): CaregiverRole.OBSERVER,
        (AuthorizationContext.API_WRITE, ShareCategory.DAILY_CARE): CaregiverRole.SECONDARY,
        (AuthorizationContext.API_READ, ShareCategory.MEDICATIONS): CaregiverRole.SECONDARY,
        (AuthorizationContext.API_WRITE, ShareCategory.MEDICATIONS): CaregiverRole.SECONDARY,
        (AuthorizationContext.API_READ, ShareCategory.CLINICAL_DOCUMENTS): CaregiverRole.PRIMARY,
        (AuthorizationContext.API_WRITE, ShareCategory.CLINICAL_DOCUMENTS): CaregiverRole.PRIMARY,
        (AuthorizationContext.API_READ, ShareCategory.OBSERVATIONS): CaregiverRole.OBSERVER,
        (AuthorizationContext.API_WRITE, ShareCategory.OBSERVATIONS): CaregiverRole.SECONDARY,
        (AuthorizationContext.API_READ, ShareCategory.FOLLOW_UPS_AND_ACTIONS): CaregiverRole.OBSERVER,
        (AuthorizationContext.API_WRITE, ShareCategory.FOLLOW_UPS_AND_ACTIONS): CaregiverRole.SECONDARY,
        (AuthorizationContext.API_READ, ShareCategory.MEANING_AND_WHAT_MATTERS): CaregiverRole.PRIMARY,
        (AuthorizationContext.API_WRITE, ShareCategory.MEANING_AND_WHAT_MATTERS): CaregiverRole.PRIMARY,
        (AuthorizationContext.EXPORT, ShareCategory.DAILY_CARE): CaregiverRole.SECONDARY,
        (AuthorizationContext.EXPORT, ShareCategory.MEDICATIONS): CaregiverRole.PRIMARY,
        (AuthorizationContext.EXPORT, ShareCategory.CLINICAL_DOCUMENTS): CaregiverRole.PRIMARY,
        (AuthorizationContext.SHARE_CREATE, ShareCategory.DAILY_CARE): CaregiverRole.PRIMARY,
        (AuthorizationContext.SHARE_CREATE, ShareCategory.MEDICATIONS): CaregiverRole.PRIMARY,
        (AuthorizationContext.SHARE_CREATE, ShareCategory.CLINICAL_DOCUMENTS): CaregiverRole.PRIMARY,
        (AuthorizationContext.DOCUMENT_DOWNLOAD, None): CaregiverRole.SECONDARY,
        (AuthorizationContext.DOCUMENT_UPLOAD, None): CaregiverRole.SECONDARY,
    }
    
    def __init__(self):
        super().__init__("caregiver_role", "Authorize based on caregiver role")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        if not request.person_id:
            return AuthorizationResult(
                decision=AuthorizationDecision.NOT_APPLICABLE,
                reason="No care context specified",
            )
        
        caregiver_roles = request.metadata.get("caregiver_roles", {})
        role_str = caregiver_roles.get(request.person_id)
        
        if not role_str:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason="No caregiver role in this care context",
                required_role="caregiver",
            )
        
        try:
            actor_role = CaregiverRole(role_str)
        except ValueError:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason=f"Invalid caregiver role: {role_str}",
            )
        
        # Check minimum role for action
        category = request.metadata.get("share_category")
        key = (request.action, category) if category else (request.action, None)
        min_role = self.MIN_ROLE_FOR_ACTION.get(key)
        
        if min_role:
            actor_level = self.ROLE_HIERARCHY.get(actor_role, 0)
            min_level = self.ROLE_HIERARCHY.get(min_role, 0)
            
            if actor_level < min_level:
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason=f"Insufficient caregiver role. Required: {min_role.value}, have: {actor_role.value}",
                    required_role=min_role.value,
                    audit_data={"actor_role": actor_role.value, "required_role": min_role.value},
                )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason=f"Caregiver role {actor_role.value} sufficient for {request.action.value}",
            matched_policies=[self.name],
            audit_data={"actor_role": actor_role.value},
        )


class ProfessionalAccessPolicy(Policy):
    """Authorize professional access to care contexts."""
    
    def __init__(self):
        super().__init__("professional_access", "Authorize verified professional access")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        professional_roles = request.metadata.get("professional_roles", {})
        
        if not request.person_id or request.person_id not in professional_roles:
            return AuthorizationResult(
                decision=AuthorizationDecision.NOT_APPLICABLE,
                reason="No professional role in this care context",
            )
        
        # Professional must be verified
        # This would be checked against ProfessionalProfile in DB
        verification_status = request.metadata.get("professional_verification_status")
        if verification_status != ProfessionalVerificationStatus.VERIFIED.value:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason="Professional credentials not verified",
                required_role="verified_professional",
            )
        
        # Check professional permissions
        prof_role = professional_roles[request.person_id]
        permissions = request.metadata.get("professional_permissions", {}).get(request.person_id, [])
        
        # Map action to required permission
        action_permission_map = {
            AuthorizationContext.API_READ: "view_shared_care_summary",
            AuthorizationContext.API_WRITE: "add_care_note",
            AuthorizationContext.DOCUMENT_ACCESS: "view_selected_documents",
            AuthorizationContext.DOCUMENT_DOWNLOAD: "view_selected_documents",
            AuthorizationContext.SHARE_CREATE: "contribute_to_community",
        }
        
        required_perm = action_permission_map.get(request.action)
        if required_perm and required_perm not in permissions:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason=f"Professional lacks required permission: {required_perm}",
                required_role="professional_with_permission",
                audit_data={"required_permission": required_perm, "actor_permissions": permissions},
            )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason=f"Verified professional with {prof_role} role authorized",
            matched_policies=[self.name],
            audit_data={"professional_role": prof_role},
        )


class StaffAccessPolicy(Policy):
    """Authorize internal staff access (least privilege)."""
    
    def __init__(self):
        super().__init__("staff_access", "Authorize internal staff with least privilege")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        staff_permissions = request.metadata.get("staff_permissions", [])
        can_access_care_data = request.metadata.get("can_access_care_data", False)
        care_data_access_expires = request.metadata.get("care_data_access_expires")
        
        if request.action == AuthorizationContext.ADMIN_ACCESS:
            if "admin" not in staff_permissions and "security_admin" not in staff_permissions:
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason="Admin permission required",
                    required_role="security_admin",
                )
        
        if request.data_classification in (DataClassification.PRIVATE_CARE, DataClassification.HIGHLY_SENSITIVE_CARE):
            if not can_access_care_data:
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason="Staff not authorized for care data access",
                    required_role="staff_with_care_data_access",
                )
            
            if care_data_access_expires:
                expiry = datetime.fromisoformat(care_data_access_expires.replace('Z', '+00:00'))
                if expiry < datetime.now(timezone.utc):
                    return AuthorizationResult(
                        decision=AuthorizationDecision.DENY,
                        reason="Care data access expired",
                        required_role="staff_with_current_care_data_access",
                    )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason="Staff access authorized",
            matched_policies=[self.name],
        )


class ConsentPolicy(Policy):
    """Authorize based on consent records."""
    
    def __init__(self):
        super().__init__("consent", "Authorize based on consent records")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        policy = get_policy(request.data_classification)
        
        if not policy.export_requires_consent and request.action != AuthorizationContext.EXPORT:
            return AuthorizationResult(
                decision=AuthorizationDecision.NOT_APPLICABLE,
                reason="Consent not required for this action/classification",
            )
        
        # Check consent records
        consent_records = request.metadata.get("consent_records", [])
        
        # For sharing actions, check specific consent
        if request.action in (AuthorizationContext.SHARE_CREATE, AuthorizationContext.EXPORT):
            has_consent = any(
                c.get("person_id") == request.person_id and
                c.get("purpose") == request.purpose and
                c.get("granted") == True and
                (not c.get("expires_at") or datetime.fromisoformat(c["expires_at"].replace('Z', '+00:00')) > datetime.now(timezone.utc))
                for c in consent_records
            )
            
            if not has_consent:
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason="No valid consent for this action",
                    required_consent=f"{request.purpose} for person {request.person_id}",
                )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason="Consent verified",
            matched_policies=[self.name],
        )


class ShareGrantPolicy(Policy):
    """Authorize based on explicit share grants."""
    
    def __init__(self):
        super().__init__("share_grant", "Authorize based on explicit share grants")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        if not request.person_id:
            return AuthorizationResult(
                decision=AuthorizationDecision.NOT_APPLICABLE,
                reason="No care context for share grant check",
            )
        
        share_grants = request.metadata.get("share_grants", [])
        
        # Find matching grant
        category = request.metadata.get("share_category")
        if not category:
            # Infer category from resource type
            category_map = {
                "care_event": ShareCategory.DAILY_CARE,
                "medication": ShareCategory.MEDICATIONS,
                "document": ShareCategory.CLINICAL_DOCUMENTS,
                "observation": ShareCategory.OBSERVATIONS,
                "follow_up": ShareCategory.FOLLOW_UPS_AND_ACTIONS,
                "person_profile": ShareCategory.MEANING_AND_WHAT_MATTERS,
            }
            category = category_map.get(request.resource_type, ShareCategory.DAILY_CARE)
        
        matching_grant = None
        for grant in share_grants:
            if (grant.get("person_id") == request.person_id and
                grant.get("category") == category.value and
                grant.get("status") == ShareGrantStatus.ACTIVE.value and
                grant.get("caregiver_id") == request.actor_account_id):
                matching_grant = grant
                break
        
        if not matching_grant:
            # Check circle scope grants
            for grant in share_grants:
                if (grant.get("person_id") == request.person_id and
                    grant.get("category") == category.value and
                    grant.get("status") == ShareGrantStatus.ACTIVE.value and
                    grant.get("scope") == ShareScope.CIRCLE.value):
                    matching_grant = grant
                    break
        
        if not matching_grant:
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason=f"No active share grant for {category.value} in this care context",
                required_role="shared_caregiver",
                audit_data={"required_category": category.value, "actor_id": request.actor_account_id},
            )
        
        # Check scope permissions
        scope = matching_grant.get("scope")
        if scope == ShareScope.NAMED.value:
            # Already matched by caregiver_id
            pass
        elif scope == ShareScope.CIRCLE.value:
            # Any caregiver in circle
            pass
        
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason=f"Share grant allows {category.value} access",
            matched_policies=[self.name],
            audit_data={"grant_id": matching_grant.get("id"), "scope": scope},
        )


class DataClassificationPolicy(Policy):
    """Enforce data classification handling requirements."""
    
    def __init__(self):
        super().__init__("data_classification", "Enforce data classification handling")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        policy = get_policy(request.data_classification)
        
        # Check encryption requirements
        if request.action in (AuthorizationContext.DOCUMENT_DOWNLOAD, AuthorizationContext.EXPORT):
            if policy.encrypt_in_transit and not request.metadata.get("https", True):
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason="Encryption in transit required for this classification",
                )
        
        # Check AI processing restrictions
        if request.action == AuthorizationContext.AI_PROCESSING:
            if not policy.ai_processing_allowed:
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason=f"AI processing not allowed for {request.data_classification.value}",
                )
            if policy.ai_minimization_required and not request.metadata.get("minimized", False):
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason="AI data minimization required for this classification",
                )
        
        # Check cache restrictions
        if request.action == AuthorizationContext.API_READ and policy.cache_ttl_seconds == 0:
            return AuthorizationResult(
                decision=AuthorizationDecision.ALLOW,
                reason="Access allowed but caching prohibited",
                matched_policies=[self.name],
                audit_data={"cache_prohibited": True},
            )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.ALLOW,
            reason="Data classification requirements met",
            matched_policies=[self.name],
        )


class ResourceOwnershipPolicy(Policy):
    """Authorize based on resource ownership."""
    
    def __init__(self):
        super().__init__("resource_ownership", "Authorize based on resource ownership")
    
    def evaluate(self, request: AuthorizationRequest, context: 'AuthorizationContext') -> AuthorizationResult:
        # For write/delete operations on owned resources
        if request.action in (AuthorizationContext.API_WRITE, AuthorizationContext.API_DELETE):
            owner_id = request.metadata.get("resource_owner_id")
            if owner_id and owner_id != request.actor_account_id:
                # Check if actor has delegated permission
                return AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason="Not the resource owner",
                    required_role="resource_owner",
                    audit_data={"resource_owner": owner_id, "actor": request.actor_account_id},
                )
        
        return AuthorizationResult(
            decision=AuthorizationDecision.NOT_APPLICABLE,
            reason="Ownership check not applicable or passed",
        )


class AuthorizationEngine:
    """Main authorization engine combining all policies."""
    
    def __init__(self):
        self.policies = [
            DenyByDefaultPolicy(),
            AuthenticationPolicy(),
            CareContextMembershipPolicy(),
            CaregiverRolePolicy(),
            ProfessionalAccessPolicy(),
            StaffAccessPolicy(),
            ConsentPolicy(),
            ShareGrantPolicy(),
            DataClassificationPolicy(),
            ResourceOwnershipPolicy(),
        ]
        self.policy_version = "1.0"
    
    def authorize(self, request: AuthorizationRequest, db: Session) -> AuthorizationResult:
        """Evaluate all policies and return final decision.
        
        Decision logic:
        - Any explicit DENY -> DENY
        - If all NOT_APPLICABLE -> DENY (deny by default)
        - If any ALLOW and no DENY -> ALLOW
        """
        # Enrich request with DB data
        self._enrich_request(request, db)
        
        deny_results = []
        allow_results = []
        not_applicable = []
        
        for policy in self.policies:
            try:
                result = policy.evaluate(request, self)
                if result.decision == AuthorizationDecision.DENY:
                    deny_results.append(result)
                elif result.decision == AuthorizationDecision.ALLOW:
                    allow_results.append(result)
                else:
                    not_applicable.append(result)
            except Exception as e:
                # Policy evaluation error -> deny for safety
                deny_results.append(AuthorizationResult(
                    decision=AuthorizationDecision.DENY,
                    reason=f"Policy evaluation error: {str(e)}",
                    matched_policies=[policy.name],
                ))
        
        # Explicit deny wins
        if deny_results:
            # Return the most specific deny
            primary_deny = deny_results[0]
            return AuthorizationResult(
                decision=AuthorizationDecision.DENY,
                reason=primary_deny.reason,
                policy_version=self.policy_version,
                matched_policies=[r.matched_policies[0] if r.matched_policies else "unknown" for r in deny_results],
                required_consent=primary_deny.required_consent,
                required_role=primary_deny.required_role,
                audit_data={
                    "deny_count": len(deny_results),
                    "allow_count": len(allow_results),
                    "primary_deny": primary_deny.audit_data,
                },
            )
        
        # If any allow, grant access
        if allow_results:
            primary_allow = allow_results[0]
            return AuthorizationResult(
                decision=AuthorizationDecision.ALLOW,
                reason=primary_allow.reason,
                policy_version=self.policy_version,
                matched_policies=[r.matched_policies[0] if r.matched_policies else "unknown" for r in allow_results],
                audit_data={
                    "allow_count": len(allow_results),
                    "primary_allow": primary_allow.audit_data,
                },
            )
        
        # Default deny
        return AuthorizationResult(
            decision=AuthorizationDecision.DENY,
            reason="No policy explicitly allowed access (deny by default)",
            policy_version=self.policy_version,
            matched_policies=["deny_by_default"],
        )
    
    def _enrich_request(self, request: AuthorizationRequest, db: Session) -> None:
        """Enrich request with database context."""
        # Get session
        session = db.query(Session).filter(Session.id == request.actor_session_id).first()
        if session:
            request.metadata["authorized_person_ids"] = session.authorized_person_ids or []
            request.metadata["caregiver_roles"] = session.authorized_caregiver_roles or {}
            request.metadata["professional_roles"] = session.authorized_professional_roles or {}
        
        # Get account
        account = db.query(Account).filter(Account.id == request.actor_account_id).first()
        if account:
            # Staff profile
            staff = db.query(StaffProfile).filter(
                StaffProfile.account_id == account.id,
                StaffProfile.is_active == True
            ).first()
            if staff:
                request.metadata["staff_permissions"] = staff.permissions or []
                request.metadata["can_access_care_data"] = staff.can_access_care_data
                request.metadata["care_data_access_expires"] = staff.care_data_access_expires.isoformat() if staff.care_data_access_expires else None
            
            # Professional profile
            prof = db.query(ProfessionalProfile).filter(
                ProfessionalProfile.account_id == account.id
            ).first()
            if prof:
                request.metadata["professional_verification_status"] = prof.verification_status.value
        
        # Get share grants if person_id provided
        if request.person_id:
            grants = db.query(CareShareGrant).filter(
                CareShareGrant.person_id == request.person_id,
                CareShareGrant.status == ShareGrantStatus.ACTIVE,
            ).all()
            request.metadata["share_grants"] = [
                {
                    "id": g.id,
                    "person_id": g.person_id,
                    "caregiver_id": g.caregiver_id,
                    "category": g.category.value,
                    "scope": g.scope.value,
                    "status": g.status.value,
                }
                for g in grants
            ]
        
        # Get consent records (placeholder - would query consent table)
        request.metadata["consent_records"] = []


# Global authorization engine instance
authorization_engine = AuthorizationEngine()


def authorize(
    db: Session,
    actor_account_id: str,
    actor_session_id: str,
    action: AuthorizationContext,
    resource_type: str,
    resource_id: str,
    person_id: Optional[str] = None,
    data_classification: DataClassification = DataClassification.PRIVATE_CARE,
    purpose: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuthorizationResult:
    """Convenience function for authorization."""
    request = AuthorizationRequest(
        actor_account_id=actor_account_id,
        actor_session_id=actor_session_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        person_id=person_id,
        data_classification=data_classification,
        purpose=purpose,
        metadata=metadata or {},
    )
    return authorization_engine.authorize(request, db)


def require_authorization(
    db: Session,
    actor_account_id: str,
    actor_session_id: str,
    action: AuthorizationContext,
    resource_type: str,
    resource_id: str,
    person_id: Optional[str] = None,
    data_classification: DataClassification = DataClassification.PRIVATE_CARE,
    purpose: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuthorizationResult:
    """Authorize and raise exception if denied."""
    result = authorize(
        db, actor_account_id, actor_session_id, action,
        resource_type, resource_id, person_id, data_classification, purpose, metadata
    )
    if result.decision == AuthorizationDecision.DENY:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=result.reason)
    return result
