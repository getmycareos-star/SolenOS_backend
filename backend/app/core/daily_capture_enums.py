"""Enums for the structured daily forms.

A caregiver logging a morning is not writing a narrative, so the daily forms
store a small controlled vocabulary rather than free text. That keeps the record
queryable - "how often has the evening dose been missed" is answerable only if
"missed" is a value the system knows - while the caregiver's own words still go
into the evidence row that everything else already reads.
"""

from enum import Enum


class MedicationStatus(str, Enum):
    """What happened with a scheduled dose.

    GIVEN and REFUSED are kept apart from MISSED deliberately. A refusal is
    something the person did, and often the most important thing in the day. A
    missed dose is an absence. Collapsing them would hide the refusal.
    """

    GIVEN = "given"
    REFUSED = "refused"
    MISSED = "missed"
    DELAYED = "delayed"
    NOT_DUE = "not_due"


class VitalType(str, Enum):
    """Vital signs a caregiver can reasonably record by hand.

    Each vital keeps its own natural unit rather than sharing one column, because
    a blood pressure reading is two numbers and a temperature is one, and forcing
    them into a single value column is how clinical records end up unreadable.
    """

    SYSTOLIC_BP = "systolic_bp"
    DIASTOLIC_BP = "diastolic_bp"
    HEART_RATE = "heart_rate"
    TEMPERATURE = "temperature"
    SPO2 = "spo2"
    RESPIRATORY_RATE = "respiratory_rate"
    BLOOD_GLUCOSE = "blood_glucose"
    WEIGHT = "weight"
    PAIN_SCORE = "pain_score"


class ScaleAxis(str, Enum):
    """One axis of a five-point observation scale.

    Each axis is a named pair of poles. The poles are what make the number
    interpretable later: a 4 on agitation and a 4 on clarity are not comparable,
    and storing only "4" would lose the fact that they point in opposite
    directions.
    """

    AGITATION = "agitation"
    CLARITY = "clarity"
    MOOD = "mood"
    APPETITE = "appetite"
    SLEEP_QUALITY = "sleep_quality"
    PAIN = "pain"


class ScaleDirection(str, Enum):
    """Which end of the axis a high number means.

    Recorded explicitly rather than inferred, because inferring it from the axis
    name is exactly the kind of quiet assumption that makes a trend mean the
    opposite of what it should.
    """

    HIGHER_IS_MORE = "higher_is_more"
    HIGHER_IS_LESS = "higher_is_less"


# The pole labels a caregiver actually sees on the buttons. Kept beside the axis
# rather than in the frontend so a printed form and the app cannot disagree.
SCALE_POLES = {
    ScaleAxis.AGITATION: (1, "Settled", 5, "Very agitated"),
    ScaleAxis.CLARITY: (1, "Clear", 5, "Very confused"),
    ScaleAxis.MOOD: (1, "Low", 5, "Bright"),
    ScaleAxis.APPETITE: (1, "None", 5, "Good appetite"),
    ScaleAxis.SLEEP_QUALITY: (1, "Restless", 5, "Slept well"),
    ScaleAxis.PAIN: (1, "No pain", 5, "Severe pain"),
}

SCALE_DIRECTIONS = {
    ScaleAxis.AGITATION: ScaleDirection.HIGHER_IS_MORE,
    ScaleAxis.CLARITY: ScaleDirection.HIGHER_IS_MORE,
    ScaleAxis.MOOD: ScaleDirection.HIGHER_IS_MORE,
    ScaleAxis.APPETITE: ScaleDirection.HIGHER_IS_MORE,
    ScaleAxis.SLEEP_QUALITY: ScaleDirection.HIGHER_IS_MORE,
    ScaleAxis.PAIN: ScaleDirection.HIGHER_IS_MORE,
}

# The natural unit for each vital. Stored so a later reader never has to guess
# whether 37 meant Celsius or Fahrenheit.
VITAL_UNITS = {
    VitalType.SYSTOLIC_BP: "mmHg",
    VitalType.DIASTOLIC_BP: "mmHg",
    VitalType.HEART_RATE: "bpm",
    VitalType.TEMPERATURE: "C",
    VitalType.SPO2: "%",
    VitalType.RESPIRATORY_RATE: "breaths/min",
    VitalType.BLOOD_GLUCOSE: "mmol/L",
    VitalType.WEIGHT: "kg",
    VitalType.PAIN_SCORE: "0-10",
}

# Vitals where a reading outside this range is worth a second look by a human.
# Used to flag, never to diagnose: the flag says "this number is unusual", and
# the system stops there.
VITAL_ATTENTION_RANGES = {
    VitalType.SYSTOLIC_BP: (90.0, 180.0),
    VitalType.DIASTOLIC_BP: (60.0, 120.0),
    VitalType.HEART_RATE: (40.0, 120.0),
    VitalType.TEMPERATURE: (35.0, 38.5),
    VitalType.SPO2: (90.0, 100.0),
    VitalType.RESPIRATORY_RATE: (8.0, 25.0),
    VitalType.BLOOD_GLUCOSE: (3.0, 22.0),
    VitalType.WEIGHT: (20.0, 300.0),
    VitalType.PAIN_SCORE: (0.0, 10.0),
}
