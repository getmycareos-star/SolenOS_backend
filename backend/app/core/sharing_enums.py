"""Enums for the trust boundary between caregivers.

Sharing a care record is not one decision. It is a set of them, each about a
different kind of information, made by different people, at different moments,
and revocable independently.

The categories below exist because the alternative - one "shared" flag - has to
be wrong for everyone. A paid carer who needs tonight's medication record does
not need the GP letters. A daughter who co-owns decisions needs the letters.
A relative who is not involved needs neither. One flag for all of them forces
the choice to be made at the widest setting and then over-shares to everyone
below it.

DEFAULT_CATEGORY below is the value used when nobody has said anything. It is
the most restrictive of the non-empty settings, because the failure that matters
is a stranger seeing a medical record. Under-sharing annoys someone who can fix
it in one tap; over-sharing cannot be undone.
"""

from enum import Enum


class ShareCategory(str, Enum):
    """Kinds of information, each shared separately."""

    DAILY_CARE = "daily_care"
    """Medication given or refused, sleep, mood, what was eaten today.

    The operational picture. Whoever is on shift today needs it.
    """

    MEDICATIONS = "medications"
    """What is prescribed, and adherence history.

    Kept apart from daily care because it is more sensitive and because the
    reader for it is narrower: the person giving tonight's dose, not the
    relative visiting on Sunday.
    """

    CLINICAL_DOCUMENTS = "clinical_documents"
    """GP letters, discharge summaries, test results.

    The most sensitive category. Named people only, never a group.
    """

    OBSERVATIONS = "observations"
    """What caregivers have noticed, promoted deliberately by whoever wrote it."""

    FOLLOW_UPS_AND_ACTIONS = "follow_ups_and_actions"
    """What was agreed, who owns it, and what is due."""

    MEANING_AND_WHAT_MATTERS = "meaning_and_what_matters"
    """The person's own preferences and character as recorded.

    Deeply personal and often the most valuable thing in the system to a new
    carer, and emphatically not a clinical fact.
    """


# Only these categories can be shared at all at this version. Anything added to
# ShareCategory without a decision here is refused rather than defaulted, so a
# new category is never quietly shareable by accident.
SHAREABLE_CATEGORIES = {
    ShareCategory.DAILY_CARE,
    ShareCategory.MEDICATIONS,
    ShareCategory.CLINICAL_DOCUMENTS,
    ShareCategory.OBSERVATIONS,
    ShareCategory.FOLLOW_UPS_AND_ACTIONS,
    ShareCategory.MEANING_AND_WHAT_MATTERS,
}

# What a caregiver gets when they have been given access but nobody has decided
# anything. Deliberately the bare operational minimum.
DEFAULT_CATEGORY = ShareCategory.DAILY_CARE


class ShareScope(str, Enum):
    """Who a grant applies to."""

    NAMED = "named"
    """One specific caregiver, decided one at a time.

    The only scope permitted for clinical documents.
    """

    CIRCLE = "circle"
    """Everyone with access to this person's care.

    Fine for what happened today. Never for anything a clinician wrote.
    """


class ShareGrantStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ShareRelationship(str, Enum):
    """The relationship a caregiver has to the person.

    Used to set the starting position, not to decide access on its own. A
    daughter is not automatically entitled to the GP letters and a paid carer is
    not automatically excluded from them; the grant says who has what.
    """

    PRIMARY_FAMILY = "primary_family"
    SECONDARY_FAMILY = "secondary_family"
    PAID_CAREGIVER = "paid_caregiver"
    FRIEND_OR_NEIGHBOUR = "friend_or_neighbour"
    PROFESSIONAL = "professional"
    OTHER = "other"


# Categories a non-family caregiver may hold at all. A neighbour who is helping
# with shopping does not get clinical documents through the back door, and this
# is enforced on the grant rather than left to whoever is setting it up.
NON_FAMILY_LIMITED_CATEGORIES = {
    ShareCategory.DAILY_CARE,
    ShareCategory.MEDICATIONS,
    ShareCategory.FOLLOW_UPS_AND_ACTIONS,
}

# Categories only a family member or the person themselves may hold.
FAMILY_ONLY_CATEGORIES = {
    ShareCategory.CLINICAL_DOCUMENTS,
    ShareCategory.MEANING_AND_WHAT_MATTERS,
}
