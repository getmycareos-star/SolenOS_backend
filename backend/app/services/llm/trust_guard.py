"""The trust guard.

Everything a language model produces passes through here before it can be
persisted or shown to a caregiver. The guard assumes the model is wrong until
checked. It does not try to make the model's answer better; it decides whether
the answer is allowed to exist, and downgrades it to something the record can
actually support if it is not.

The four rules it enforces, in order of severity:

1. Nothing survives that the record does not support. Citations that are not in
   the permitted evidence set are dropped and reported, never silently kept.
2. A number is never invented. If the model supplies a confidence with no basis
   in the permitted evidence, it becomes null rather than a plausible-looking
   float.
3. Uncertainty is mandatory. An empty possible_context is a defect, so one is
   synthesised from what the guard actually knows is missing.
4. Claims the record cannot support at all - diagnoses, medication instructions,
   invented certainties - are rejected outright rather than qualified, because a
   hedged diagnosis is still a diagnosis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from app.core.llm_enums import EvidenceClaimType, GuardVerdict

_MAX_TEXT_LENGTH = 2000

_PROHIBITED_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(previous\s+|prior\s+)?instructions?",
    r"disregard\s+(all\s+|any\s+)?(previous\s+|prior\s+)?",
    r"forget\s+(all\s+|any\s+)?(previous\s+|prior\s+)?instructions?",
    r"pretend\s+that\s+",
    r"act\s+as\s+if\s+",
    r"you\s+are\s+(now|a)\s+(doctor|physician|clinician|nurse|psychiatrist)",
    r"override\s+(safety|security|policy)",
    r"bypass\s+(safety|security|policy|restriction)",
    r"jailbreak",
    r"system\s+prompt",
]

# Diagnostic and prognostic language. Written to catch the forms that actually
# appear - hyphenated stage adjectives, "a sign of progression" - rather than
# only the tidy textbook phrasings.
_DIAGNOSTIC_PATTERNS = [
    r"\bdiagnos(e|ed|tic)\b",
    r"\b(has|have|having|is|are)\s+(an?\s+)?(early|late|advanced|moderate|severe|prodromal)\s+[\w-]*\s*(dementia|alzheimers|alzheimer'?s|lewy\s+body|vascular|frontotemporal)\b",
    r"\b(dementia|alzheimers|alzheimer'?s|vascular\s+dementia|lewy\s+body)\s+(progression|stage|is\s+progressing)\b",
    r"\b(sign|signs|marker|markers|evidence|indicator|indicators)\s+of\s+(progression|dementia|impairment|decline)\b",
    r"\bprogressive\s+(deterioration|condition|decline)\b",
    r"\bcognitive\s+(decline|impairment)\s+(is|has)\b",
    r"\bsuffering\s+from\b",
    # "condition is <word>" catches "his condition is dementia" but must not
    # swallow the negation: "her condition is not established" would otherwise
    # match on "condition is not" and consume the very word that makes the
    # sentence a denial rather than a diagnosis.
    r"\bcondition\s+is\s+(?!not\b|no\b|un\w+|never\b)([\w-]+\s+){0,2}(dementia|alzheimers|alzheimer'?s|stroke|dementia|declin\w+|impair\w+|illness|disease|disorder)\b",
    r"\bstage\s+\d\b",
    r"\bprognosis\b",
    r"\bwill\s+live\b",
    r"\bexpected\s+lifespan\b",
    r"\bmildly\s+moderately\s+severe(ly)?\s+impaired\b",
    r"\bthis\s+is\s+(early|late|advanced|moderate|severe)\b",
    # Acute conditions a layperson names directly. "This is a stroke" matched
    # nothing above, because the earlier "this is ..." rule required an
    # adjective like "severe". A bald assertion of an acute event is the single
    # most dangerous sentence this system could pass, and it was passing.
    r"\bthis\s+is\s+(a\s+|an\s+)?(stroke|heart\s+attack|cardiac\s+arrest|seizure|sepsis|infection|haemorrhage|hemorrhage|stroke|TIA|transient\s+ischaemic|unconsciousness|coma|poisoning|overdose|choking|anaphylaxis)\b",
    r"\b(he|she|they|this)\s+(is|are|has|have|had)\s+(a\s+|an\s+)?(stroke|heart\s+attack|cardiac\s+arrest|seizure|sepsis|anaphylaxis|unconscious|unconsciousness|intoxicated|overdosed)\b",
    # "is having a seizure" and "has Alzheimer's disease" state the same thing
    # through different grammar, and matched nothing above. Naming a condition
    # outright is the plainest way a model makes a diagnosis, so it is matched
    # directly instead of relying on the hedged constructions.
    r"\b(is|are)\s+(having|showing|experiencing|displaying|developing)\s+(a\s+|an\s+)?(stroke|seizure|sepsis|anaphylaxis|infection|hallucinations?|delirium|chest\s+pain)\b",
    r"\b(has|have|had)\s+(early|late|advanced|moderate|severe|some)\s+(dementia|alzheimers|alzheimer'?s)\b",
    # "He has Alzheimer's disease" names no stage, and "diagnos(e|ed|tic)"
    # above does not catch it either. Named neurodegenerative conditions after
    # a plain present-tense verb are asserted as fact, so they are refused.
    r"\b(has|have|had)\s+(alzheimer'?s|alzheimers|dementia|vascular\s+dementia|lewy\s+body\s+dementia|parkinson'?s|frontotemporal\s+dementia)\b",
    r"\b(is|are)\s+(diabetic|hypoglycemic|hypoglycaemic|hyperglycemic|hyperglycaemic|anaemic|anemic|dehydrated|septic|cognitively\s+impaired)\b",
    r"\b(he|she|they)\s+(is|are)\s+(demented|diabetic|hypoglycemic|hypoglycaemic|hyperglycemic|hyperglycaemic|anaemic|anemic|dehydrated|septic|cognitively\s+impaired)\b",
    r"\blook\s+like\s+(a\s+)?(stroke|seizure|infection|heart\s+attack)\b",
    r"\bwhat\s+(is|was)\s+(the\s+)?(diagnosis|prognosis|cause)\s*\??\s*$",
]

# An assertion of a diagnosis is a diagnosis. Saying that the record does not
# establish one is the opposite, and is the behaviour the prompts ask for.
# Without this distinction the guard threw away hedged denials - "the evidence
# does not establish a cause for her condition" tripped "condition is \w+" - so
# the safest-sounding answer was the one most likely to be blocked.
_NEGATION_MARKERS = (
    "not established",
    "not diagnosed",
    "no diagnosis",
    "does not establish",
    "does not show",
    "do not know",
    "dont know",
    "cannot tell",
    "can't tell",
    "not clear",
    "unclear",
    "not possible to say",
    "no way to know",
    "not confirmed",
    "no confirmed",
    "not yet known",
    "is not known",
    "are not known",
    "unknown",
    "not documented",
    "nothing in the record",
    "record does not",
    "no evidence of",
    "not what",
    "rather than",
    "no cause",
    "not a diagnosis",
    "no sign of",
    "not shown",
)


def _states_uncertainty_before(text: str, match_start: int, match_end: int = 0) -> bool:
    """Is the diagnostic phrase actually a denial rather than an assertion?

    Looks around the match rather than only before it, because English puts the
    hedge on either side: "does not establish a cause for her condition" leads,
    while "her condition is not established by the record" follows. Reading only
    backwards blocked the second form and allowed the first, which is the
    opposite of what a guard should do.
    """
    before = text[max(0, match_start - 120) : match_start].lower()
    after = text[match_end : match_end + 90].lower()
    return any(marker in before for marker in _NEGATION_MARKERS) or any(
        marker in after for marker in _NEGATION_MARKERS
    )

_MEDICAL_ACTION_PATTERNS = [
    r"\b(you|we|i)\s+should\s+(start|stop|increase|decrease|reduce|change|switch|add)\b",
    r"\b(you|we|i)\s+need\s+to\s+(start|stop|increase|decrease|reduce|change|switch|add)\b",
    r"\b(you|we|i)\s+must\s+(start|stop|increase|decrease|reduce|change|switch|add)\b",
    r"\b(recommend|suggest)\s+(starting|stopping|increasing|decreasing|reducing|changing|switching|adding)\b",
    r"\b(stop|start|increase|decrease|reduce|double|skip|omit|hold)\s+"
    r"(the\s+|his\s+|her\s+|their\s+|a\s+)?((evening|morning|night|nightly|bedtime|daily|regular|usual)\s+)?"
    r"(medication|medicine|meds|pill|pills|dose|dosage|tablet|tablets|capsule|insulin|prescription)\b",
    r"\b(dose|dosage)\s+should\s+be\b",
    r"\bgive\s+(him|her|them|the\s+person)\s+\d",
    r"\b(it\s+is|it's)\s+safe\s+to\s+(stop|start|skip|increase|decrease)\b",
    r"\bdiagnos(e|ed)\s+with\b",
]

_CERTAINTY_MARKERS = [
    "definitely",
    "certainly",
    "without doubt",
    "undeniably",
    "it is clear that",
    "clearly",
    "obviously",
    "always",
    "never",
    "must be",
    "is the cause",
    "caused by",
    "proven",
    "guaranteed",
]

_FALSE_REASSURANCE_MARKERS = [
    "nothing to worry about",
    "no need to worry",
    "don't worry",
    "do not worry",
    "probably nothing serious",
    "nothing serious",
    "not serious",
    "likely fine",
    "probably fine",
    "probably okay",
    "probably ok",
    "probably alright",
    "should be fine",
    "should be ok",
    "everything appears stable",
    "everything is stable",
    "everything looks normal",
    "appears stable",
    "looks stable",
    "seems stable",
    "all good",
    "nothing to be concerned about",
    "not a concern",
    "nothing unusual",
    # Paraphrases of the same reassurance. A model that cannot say "no need to
    # worry" will say "he seems fine" or "there is nothing to be concerned
    # about", and a guard that only catches the literal phrasing stops being a
    # guard the moment the model chooses different words. A record cannot
    # support reassurance about a person's state, so these are rejected the same
    # way whichever way they are phrased.
    "seems fine",
    "seems okay",
    "seems well",
    "looks fine",
    "looks okay",
    "looks well",
    "seems normal",
    "looks normal",
    "nothing to be concerned",
    "nothing to worry",
    "not an emergency",
    "no emergency",
    "there is no cause for concern",
    "no cause for concern",
    "is fine",
    "are fine",
    "is well",
    "are well",
    "all is well",
    "nothing has changed",
    "nothing to report",
    "take no action",
    "no action needed",
]

_OVERCONFIDENT_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(marker) for marker in _CERTAINTY_MARKERS) + r")\b",
    re.IGNORECASE,
)

# Markers of a statement that actually concedes something. A possible_context
# lacking all of these is restating the finding rather than naming what is open.
_UNCERTAINTY_MARKERS = (
    "not ",
    "no ",
    "never",
    "unknown",
    "unclear",
    "unsure",
    "may ",
    "might",
    "could",
    "perhaps",
    "possibly",
    "if ",
    "whether",
    "unless",
    "would ",
    "assume",
    "unverified",
    "unconfirmed",
    "check",
    "confirm",
    "ask",
    "assess",
    "record does not",
    "not established",
    "not documented",
    "not known",
    "still open",
    "outstanding",
)

_UNKNOWN_SUBSTITUTIONS = {
    "unknown": "not established by the record",
    "n/a": None,
    "none": None,
    "not applicable": None,
    "": None,
    "-": None,
}


@dataclass
class GuardResult:
    verdict: GuardVerdict
    payload: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    qualifications: List[str] = field(default_factory=list)
    dropped_evidence_ids: List[str] = field(default_factory=list)
    output_evidence_ids: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    claims: List[EvidenceClaimType] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return self.verdict in (GuardVerdict.ACCEPTED, GuardVerdict.QUALIFIED)


class TrustGuard:
    """Validates one model response against one permitted evidence set."""

    def __init__(
        self,
        permitted_evidence_ids: Optional[Sequence[str]] = None,
        evidence_texts: Optional[Dict[str, str]] = None,
        allow_diagnostic: bool = False,
        allow_medical_action: bool = False,
        require_evidence: bool = True,
    ) -> None:
        self.permitted: Set[str] = set(permitted_evidence_ids or [])
        self.evidence_texts: Dict[str, str] = dict(evidence_texts or {})
        self.allow_diagnostic = allow_diagnostic
        self.allow_medical_action = allow_medical_action
        self.require_evidence = require_evidence

    def guard_insight(self, raw: Optional[Dict[str, Any]]) -> GuardResult:
        """Guard the standard {summary, evidence_ids, confidence, possible_context} shape."""
        reasons: List[str] = []
        qualifications: List[str] = []
        dropped: List[str] = []

        if not isinstance(raw, dict):
            return GuardResult(
                verdict=GuardVerdict.REJECTED,
                reasons=["model response was not an object"],
            )

        summary = _clean_text(raw.get("summary") or raw.get("description") or "")
        if not summary:
            return GuardResult(verdict=GuardVerdict.REJECTED, reasons=["model returned no summary"])

        cited, dropped = self._filter_citations(raw.get("evidence_ids"))
        if dropped:
            reasons.append(
                "dropped {} citation(s) not present in the permitted evidence set: {}".format(
                    len(dropped), ", ".join(dropped)
                )
            )
            qualifications.append(
                "The model referenced material outside the supplied record; those references were removed."
            )

        text_blob = _collect_text(raw)
        verdict, hard_reasons = self._screen_text(text_blob)
        if hard_reasons:
            return GuardResult(
                verdict=GuardVerdict.REJECTED,
                reasons=reasons + hard_reasons,
                dropped_evidence_ids=dropped,
            )
        reasons.extend(hard_reasons)

        summary, hedge_notes = _strip_certainty(summary)
        qualifications.extend(hedge_notes)
        if hedge_notes:
            reasons.append("removed unsupported certainty language from the summary")

        claims = classify_claims(text_blob)

        confidence = self._resolve_confidence(raw.get("confidence"), cited, claims, qualifications, reasons)

        if self.require_evidence and not cited:
            reasons.append("no citation survived; this is an ungrounded statement")
            qualifications.append(
                "This is not supported by the record. Treat it as a question to ask, not a finding."
            )

        possible_context = self._resolve_possible_context(
            raw.get("possible_context"), cited, dropped, summary
        )

        sanitized = dict(raw)
        sanitized["summary"] = summary
        sanitized["evidence_ids"] = cited
        sanitized["confidence"] = confidence
        sanitized["possible_context"] = possible_context
        for key, value in list(sanitized.items()):
            if isinstance(value, str):
                sanitized[key] = value[:_MAX_TEXT_LENGTH]
            elif isinstance(value, list):
                sanitized[key] = [_truncate(v) for v in value]

        return GuardResult(
            verdict=GuardVerdict.QUALIFIED if (reasons or qualifications) else GuardVerdict.ACCEPTED,
            payload=sanitized,
            reasons=reasons,
            qualifications=_dedupe(qualifications),
            dropped_evidence_ids=dropped,
            output_evidence_ids=cited,
            confidence=confidence,
            claims=claims,
        )

    def guard_conversation(self, raw: Optional[Dict[str, Any]]) -> GuardResult:
        """Guard a conversational turn.

        Conversation is the one surface where refusing to answer is the correct
        behaviour most of the time, so the guard here is narrower: it screens
        for injected instructions and unsupported clinical instructions, but it
        does not require the reply to be evidence-backed, because most replies
        legitimately are not about the person at all.
        """
        reasons: List[str] = []
        qualifications: List[str] = []
        dropped: List[str] = []

        if not isinstance(raw, dict):
            return GuardResult(verdict=GuardVerdict.REJECTED, reasons=["model response was not an object"])

        reply = _clean_text(raw.get("reply") or "")
        if not reply:
            return GuardResult(verdict=GuardVerdict.REJECTED, reasons=["model returned no reply"])

        cited, dropped = self._filter_citations(raw.get("grounded_claims"))
        if dropped:
            reasons.append("dropped ungrounded conversation claim(s): " + ", ".join(dropped))
            qualifications.append("A claim about the person was removed for lack of evidence.")

        verdict, hard_reasons = self._screen_text(reply)
        if hard_reasons:
            return GuardResult(
                verdict=GuardVerdict.REJECTED,
                reasons=reasons + hard_reasons,
                dropped_evidence_ids=dropped,
            )

        reply, hedge_notes = _strip_certainty(reply)
        if hedge_notes:
            reasons.append("removed unsupported certainty language from the reply")

        safety_note = _clean_text(raw.get("safety_note") or "")

        claims = classify_claims(reply)

        # A confidence is meaningful here only when the reply actually made a
        # claim about the person and cited something for it. A conversational
        # reply that touches only the caregiver's own situation has no claim to
        # be confident about, so _resolve_confidence returns None for it. This
        # used to be hardcoded to None for every turn, which meant a grounded
        # answer could never carry the one number a caregiver is entitled to see.
        confidence = self._resolve_confidence(
            raw.get("confidence"), cited, claims, qualifications, reasons
        )

        sanitized = dict(raw)
        sanitized["reply"] = reply
        sanitized["grounded_claims"] = cited
        sanitized["safety_note"] = safety_note or None
        sanitized["possible_context"] = raw.get("possible_context") or None
        sanitized["confidence"] = confidence

        return GuardResult(
            verdict=GuardVerdict.QUALIFIED if (reasons or qualifications) else GuardVerdict.ACCEPTED,
            payload=sanitized,
            reasons=reasons,
            qualifications=_dedupe(qualifications),
            dropped_evidence_ids=dropped,
            output_evidence_ids=cited,
            confidence=confidence,
            claims=claims,
        )

    def _screen_text(self, text: str) -> "tuple[GuardVerdict, List[str]]":
        reasons: List[str] = []
        lowered = text.lower()

        for pattern in _PROHIBITED_PATTERNS:
            if re.search(pattern, lowered):
                reasons.append(f"model output matched a prohibited instruction pattern: {pattern}")
        if reasons:
            return GuardVerdict.REJECTED, reasons

        if not self.allow_diagnostic:
            for pattern in _DIAGNOSTIC_PATTERNS:
                match = re.search(pattern, lowered)
                if not match:
                    continue
                # A hedged denial is not a diagnosis. Blocking it would mean the
                # guard discards the most honest sentence a model can produce
                # while letting bald assertions through.
                if _states_uncertainty_before(lowered, match.start(), match.end()):
                    continue
                reasons.append(
                    "model output made a diagnostic or prognostic claim, which the record cannot support: "
                    + pattern
                    )
        if not self.allow_medical_action:
            for pattern in _MEDICAL_ACTION_PATTERNS:
                if re.search(pattern, lowered):
                    reasons.append(
                        "model output gave a medication or treatment instruction: " + pattern
                    )
        if reasons:
            return GuardVerdict.REJECTED, reasons

        found: List[str] = []
        for marker in _FALSE_REASSURANCE_MARKERS:
            if marker in lowered:
                found.append(marker)
        if found:
            return GuardVerdict.REJECTED, [
                "model output offered false reassurance ({})".format(", ".join(found))
            ]

        return GuardVerdict.ACCEPTED, []

    def _filter_citations(self, raw_ids: Any) -> "tuple[List[str], List[str]]":
        if not isinstance(raw_ids, (list, tuple)):
            return [], []
        kept: List[str] = []
        dropped: List[str] = []
        for value in raw_ids:
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if not candidate:
                continue
            if candidate in self.permitted:
                if candidate not in kept:
                    kept.append(candidate)
            elif candidate not in dropped:
                dropped.append(candidate)
        return kept, dropped

    def _resolve_confidence(
        self,
        raw_confidence: Any,
        cited: Sequence[str],
        claims: Sequence[EvidenceClaimType],
        qualifications: List[str],
        reasons: List[str],
    ) -> Optional[float]:
        """Return a confidence only when the record genuinely supports one.

        Causal, predictive and normative claims are not things a confidence
        number can honestly describe, so they get null regardless of what the
        model proposed.
        """
        unsupported_types = {
            EvidenceClaimType.CAUSAL,
            EvidenceClaimType.PREDICTIVE,
            EvidenceClaimType.NORMATIVE,
        }
        if claims and any(claim in unsupported_types for claim in claims):
            reasons.append("confidence withheld: the claim is causal, predictive, or normative")
            return None

        if not cited:
            reasons.append("confidence withheld: no surviving citation")
            return None

        value = _as_float(raw_confidence)
        if value is None:
            reasons.append("confidence withheld: the model did not supply a usable value")
            return None
        if value >= 1.0:
            # An LLM stating 1.0 is asserting certainty, which is a claim about
            # the world, not a calibrated probability. Cap it rather than
            # passing absolute confidence through to a caregiver.
            reasons.append("model asserted certainty; confidence capped below 1.0")
            qualifications.append(
                "The model was certain about this. Nothing in a care record is certain, "
                "so treat this as a strong reading rather than a settled fact."
            )
            return 0.95
        if value <= 0.0:
            reasons.append("confidence withheld: a value of zero is not a usable reading")
            return None
        return round(min(max(value, 0.05), 0.95), 3)

    def _resolve_possible_context(
        self,
        raw_value: Any,
        cited: Sequence[str],
        dropped: Sequence[str],
        summary: str = "",
    ) -> str:
        """Return an uncertainty statement that genuinely states an uncertainty.

        An empty possible_context is the obvious failure, and it is handled
        below. The quieter failure is a model that fills the field by restating
        its own summary, which passes an emptiness check while telling the
        caregiver nothing they did not already have. Local models do this
        constantly, so it is checked explicitly and replaced.
        """
        text = _clean_text(raw_value or "")
        if text and _states_an_uncertainty(text, summary):
            return text
        if text:
            return self._synthesise_possible_context(cited, dropped, reason="the model's stated context was not an uncertainty")
        return self._synthesise_possible_context(cited, dropped, reason=None)

    def _synthesise_possible_context(
        self,
        cited: Sequence[str],
        dropped: Sequence[str],
        reason: Optional[str] = None,
    ) -> str:
        parts: List[str] = []
        if reason:
            parts.append(reason.capitalize() + ".")
        if dropped:
            parts.append(
                "The record contains no evidence for part of what was drafted ({}); that part is unverified.".format(
                    ", ".join(dropped)
                )
            )
        if not cited:
            parts.append(
                "The available record does not establish this. Confirming it with the person, the caregiver present, "
                "or the clinician would be needed before relying on it."
            )
        else:
            parts.append(
                "What is not established is why this is the case, whether it is a change from the person's usual "
                "state, and whether anyone has assessed it."
            )
        return " ".join(parts)


def _states_an_uncertainty(text: str, summary: str = "") -> bool:
    """Does this actually name something that is unknown?

    Two ways to fail: it names nothing (no uncertainty marker at all), or it is
    a restatement of the finding wearing the field's name. Both are caught.
    """
    lowered = (text or "").lower()
    if not any(marker in lowered for marker in _UNCERTAINTY_MARKERS):
        return False
    if summary:
        normalised_summary = " ".join(summary.lower().split()).rstrip(".")
        normalised_text = " ".join(lowered.split()).rstrip(".")
        if normalised_text == normalised_summary:
            return False
        # A restatement with a few words swapped is still a restatement.
        summary_tokens = set(normalised_summary.split())
        text_tokens = set(normalised_text.split())
        if text_tokens and len(text_tokens & summary_tokens) / len(text_tokens) > 0.85:
            return False
    return True


def classify_claims(text: str) -> List[EvidenceClaimType]:
    lowered = (text or "").lower()
    claims: List[EvidenceClaimType] = []
    if any(marker in lowered for marker in _CERTAINTY_MARKERS):
        claims.append(EvidenceClaimType.FACTUAL)
    if re.search(
        r"\b(caused by|caused|causing|causes|due to|because of|result of|led to|"
        r"triggered by|induced by|from the)\b",
        lowered,
    ):
        claims.append(EvidenceClaimType.CAUSAL)
    if any(
        marker in lowered
        for marker in (
            "diagnos",
            "condition is",
            "progression",
            "stage",
            "prognosis",
            "dementia",
        )
    ):
        claims.append(EvidenceClaimType.DIAGNOSTIC)
    if re.search(r"\b(will|likely to|going to|expected to|predict)\b", lowered):
        claims.append(EvidenceClaimType.PREDICTIVE)
    if re.search(r"\b(should|must|ought to|need to)\b", lowered):
        claims.append(EvidenceClaimType.NORMATIVE)
    return _dedupe(claims)


def _strip_certainty(text: str) -> "tuple[str, List[str]]":
    """Soften unsupported certainty rather than rejecting the whole output.

    A model that says "this is definitely the cause" is wrong about the cause,
    but the observation underneath it is often real. Stripping the certainty
    keeps the observation and removes the claim, which is what the caregiver can
    actually use.
    """
    if not text:
        return text, []
    notes: List[str] = []
    cleaned = text
    for marker in _CERTAINTY_MARKERS:
        pattern = re.compile(r"\b" + re.escape(marker) + r"\b", re.IGNORECASE)
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            notes.append("removed unsupported certainty language: " + marker)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\b(is|are|was|were|has|have)\s{2,}", r"\1 ", cleaned)
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned, notes


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in _UNKNOWN_SUBSTITUTIONS:
        return ""
    return text


def _collect_text(raw: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, value in raw.items():
        if key in ("evidence_ids", "grounded_claims", "confidence"):
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.extend(str(v) for v in item.values() if isinstance(v, str))
        elif isinstance(value, dict):
            parts.extend(str(v) for v in value.values() if isinstance(v, str))
    return "\n".join(parts)


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _truncate(value: Any) -> Any:
    if isinstance(value, str):
        return value[:_MAX_TEXT_LENGTH]
    if isinstance(value, dict):
        return {key: _truncate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate(item) for item in value]
    return value


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
