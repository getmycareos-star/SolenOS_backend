# SolenOS professional accounts

Professionals use a separate account and authentication namespace. A professional does not need to create a caregiver account, a care recipient, or a Living Care Record.

## Current contract

- POST /api/v1/professional-auth/register creates an independent professional profile and bearer session.
- POST /api/v1/professional-auth/login authenticates by email/password.
- GET /api/v1/professional-auth/me returns only the signed-in professional's own profile and review status.
- POST /api/v1/professional-auth/logout revokes the presented professional session.
- DELETE /api/v1/professional-auth/account deletes the professional account and its sessions.

Professional sessions are stored separately from caregiver sessions and accepted only by the professional-auth route family. A professional token must not authorize caregiver APIs or access any caregiver Living Care Record.

New profiles start as pending_review. Signup is not credential verification: SolenOS must review professional identity and qualifications before granting verified status or publishing privileges. Until a moderation workflow exists, the profile page must say review and community publishing are pending; it must not imply that the professional is verified or can already publish advice.

## Recommended Community invitation

**Are you a dementia-care professional?**  
Share practical knowledge with family caregivers navigating dementia care. Create a professional profile to introduce your experience, areas of expertise, and the support you can offer. SolenOS reviews professional profiles before granting verified status or publishing access.

**CTA:** Join as a professional

This is a professional contribution and profile pathway, not a caregiver signup. Community contributions must remain separate from a caregiver's Living Care Record unless the caregiver explicitly chooses to capture information there.

## Security gaps not claimed as implemented

Email verification, password reset, MFA, persistent rate limiting, and account recovery are not included in this change. Account deletion removes the professional profile and session rows. Retention/deletion of any future published community contributions needs an explicit policy and implementation before publication is enabled.
