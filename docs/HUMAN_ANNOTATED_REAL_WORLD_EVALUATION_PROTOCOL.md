# Human-annotated real-world evaluation protocol

Status: protocol prepared. No real-world records have been collected or evaluated.

## Purpose

Evaluate whether SolenOS preserves and retrieves meaning, source, timing, uncertainty, disagreement, and correction history in longitudinal care records supplied by real caregivers. This evaluates record continuity and evidence handling; it is not a diagnostic-accuracy study or authorization for clinical decision-making.

## Entry conditions — all required

- Document a lawful basis and informed permission appropriate to the participant's role and jurisdiction.
- Obtain permission from the person whose care information is represented, or document legally valid representative authority where applicable.
- Explain purpose, retention, access, withdrawal, and review arrangements.
- Use a documented de-identification procedure and re-identification risk review. Remove direct identifiers and generalize dates, locations, institutions, rare occupations, and unusual event combinations where needed. Removing names alone does not anonymize longitudinal histories.
- Store source material separately from production in restricted, access-logged storage. Give annotators only the minimum approved material.
- Assign a data steward to verify consent and de-identification.
- Complete any required ethics, privacy, institutional, contractual, and regulatory review before collection. Obtain jurisdiction-specific advice; this protocol does not itself establish compliance.

Exclude any record with unclear consent/authority, inadequate de-identification, doubtful source integrity, or incompatible retention requirements. Never copy production caregiver data into a development or benchmark corpus.

## Proposed pilot

Start with 30 independently sourced longitudinal histories, each with at least 10 temporally ordered source items and at least two source types/reporters where available. This is a proposed target, not a claim that records have been recruited.

Seek variation in routine/disrupted care, multiple reporters, missing/partial/delayed/stale/inaccessible evidence, contradictions and corrections, duplicate copies versus repeated events, concurrent care threads, uncertain dates, and sparse documentation. Do not manufacture missing categories. Mark unrepresented categories as not evaluated.

## Annotation unit and codebook

The unit is a source-grounded claim or question in a defined care-history thread, with its source span and timeline context. Reviewers independently record:

1. Claim status: observed, reported, inferred, planned, completed, corrected, disputed, unknown, or unsupported.
2. Speaker, reporter, observer, uploader, and subject; unknown roles remain unknown.
3. Event/effective time and recorded/received time, precision, timezone if known, and whether estimated.
4. Exact source item/span and support level: direct, partial, contextual, absent, or inaccessible.
5. Uncertainty: known, uncertain, missing, ambiguous, or needs clarification.
6. Contradictions: both sides, thread, resolution status, and whether later evidence actually resolves the conflict.
7. Correction lineage: original claim, correction, source, actor, rationale, and supersession relationship.
8. Thread/person identity; similar labels alone never justify merging.
9. Safety boundary: unsupported diagnosis, causality, medication instruction, completion, or future-risk claim.
10. Retrieval completeness: relevant historical evidence, unresolved questions, and contradictions retrieved or missed.

Freeze and version the annotation manual before the evaluation run. Expected labels must be authored independently of SolenOS output.

## Independent review and disagreement

- Two trained reviewers annotate every history independently, blinded to SolenOS output and each other's labels.
- At least one reviewer must have relevant care-record/clinical-context expertise; the second must be trained in provenance and longitudinal record review. Record qualifications and conflicts.
- A third qualified adjudicator resolves disagreements only after both original annotations are locked.
- Preserve both annotations, disagreement category, adjudicated label/rationale, pseudonymous reviewer IDs, codebook version, and timestamps.
- Never silently replace disagreement with a consensus label.

Report raw agreement and a suitable chance-corrected statistic: Cohen's kappa for two fixed categorical reviewers, weighted kappa for ordered labels, or Krippendorff's alpha where missingness or multiple raters requires it. Include confidence intervals, category prevalence, and confusion matrices; kappa alone can mislead for rare labels.

## Metrics and hard failures

Run the frozen histories through a version-pinned SolenOS build only after labels are locked. Preserve input/output hashes, application commit, schema head, and model/configuration versions.

Report per-dimension precision/recall and confusion matrices for claim status; exact match for role attribution; temporal precision; valid/source-supported citations; contradiction preservation and false-resolution rate; correction-lineage completeness; duplicate-versus-recurrence discrimination; thread merge false positives/negatives; historical retrieval recall; unsupported inference rates by category; and cross-person authorization failures. Publish denominators, exclusions, missing labels, and per-case outcomes. Do not blend safety-critical misses into one average.

Any confirmed cross-person disclosure, fabricated/wrong source, silent contradiction/correction erasure, invented attribution, silence-as-negative evidence, plan-as-completion, unsupported diagnosis/causality/medication/risk claim, hidden material uncertainty, or unrelated-thread merge fails the run regardless of aggregate score.

## Reporting and current dependencies

Every report must include dataset version/hash, consent and de-identification review, sample composition, annotation manual, reviewer qualifications, inter-rater agreement, adjudication counts, application commit, metrics, hard failures, exclusions, and limitations. The first pilot is for error discovery, not a marketing claim or clinical validation.

Current status:
- Protocol: prepared.
- Real histories recruited: none verified.
- Consent/de-identification review: not performed.
- Independent reviewers recruited: none verified.
- Human annotation/adjudication: none.
- Real-world accuracy: not measured.

External dependencies: lawful access to suitable histories, a data steward/privacy review, two independent qualified reviewers, and an adjudicator. Until those exist, real-world evaluation is not started.
