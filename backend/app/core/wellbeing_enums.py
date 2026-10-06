"""Enums for caregiver wellbeing.

The caregiver is a person with a record of their own, not an attribute of the
person being cared for. Keeping the two apart is the whole point of this module:
a daughter logging that she has not slept in four nights is describing herself,
and folding that into her mother's clinical history would both misrepresent her
mother and expose something the daughter chose to say to a system that was not
told she was the subject.

The vocabulary is small and concrete on purpose. "Wellbeing" invites a score
that sounds clinical and is not, so each axis below names something a caregiver
can actually recognise in themselves.
"""

from enum import Enum


class WellbeingAxis(str, Enum):
    """One thing about the caregiver's own state."""

    SLEEP = "sleep"
    STRESS = "stress"
    LONELINESS = "loneliness"
    PHYSICAL_STRAIN = "physical_strain"
    TIME_FOR_SELF = "time_for_self"


# Each axis is a named pair of poles, and the direction is stored with the score
# rather than inferred. "3" on sleep and "3" on stress point in opposite
# directions, and a trend that silently mixed them up would say the exact
# opposite of what the caregiver meant.
WELLBEING_POLES = {
    WellbeingAxis.SLEEP: (1, "Not sleeping", 5, "Sleeping well"),
    WellbeingAxis.STRESS: (1, "Coping", 5, "At my limit"),
    WellbeingAxis.LONELINESS: (1, "Company", 5, "Very alone"),
    WellbeingAxis.PHYSICAL_STRAIN: (1, "Manageable", 5, "Exhausted"),
    WellbeingAxis.TIME_FOR_SELF: (1, "None", 5, "Some time"),
}

# Recorded explicitly. Anything not listed here is HIGHER_IS_MORE, because the
# general case is that a higher number means more of the thing named.
WELLBEING_DIRECTIONS = {
    WellbeingAxis.SLEEP: "higher_is_better",
    WellbeingAxis.STRESS: "higher_is_worse",
    WellbeingAxis.LONELINESS: "higher_is_worse",
    WellbeingAxis.PHYSICAL_STRAIN: "higher_is_worse",
    WellbeingAxis.TIME_FOR_SELF: "higher_is_better",
}


class WellbeingDirection(str, Enum):
    HIGHER_IS_MORE = "higher_is_more"
    HIGHER_IS_BETTER = "higher_is_better"
    HIGHER_IS_WORSE = "higher_is_worse"


def direction_for(axis: WellbeingAxis) -> WellbeingDirection:
    return WellbeingDirection(WELLBEING_DIRECTIONS.get(axis, "higher_is_more"))


class WellbeingSignal(str, Enum):
    """What the system noticed, stated as an observation and never a conclusion.

    These describe what was recorded, not what it means. There is deliberately
    no "caregiver is at risk of burnout" here: that is a clinical judgement this
    system has no standing to make from a four-point scale.
    """

    SUSTAINED_HIGH_STRESS = "sustained_high_stress"
    SLEEP_NOT_RECORDED_RECENTLY = "sleep_not_recorded_recently"
    SUSTAINED_POOR_SLEEP = "sustained_poor_sleep"
    LONG_WITHOUT_TIME_FOR_SELF = "long_without_time_for_self"
    IMPROVING = "improving"


class SupportKind(str, Enum):
    """A concrete thing that exists, not a recommendation to seek help.

    Naming a service this system cannot vouch for would be worse than saying
    nothing. These are the things a caregiver can act on tonight.
    """

    SHARE_THE_ROTATION = "share_the_rotation"
    ASK_SOMEONE_FOR_A_SHIFT = "ask_someone_for_a_shift"
    RESCHEDULE_A_VISIT = "reschedule_a_visit"
    TALK_TO_SOMEONE = "talk_to_someone"
    GP_APPOINTMENT = "gp_appointment"
    CARERS_RESPITE = "carers_respite"
