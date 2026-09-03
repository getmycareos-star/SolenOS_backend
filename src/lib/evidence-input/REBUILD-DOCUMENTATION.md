# Evidence & Input Intelligence — Rebuild Documentation

## What Was Rebuilt and Why

### Summary

The previous Evidence & Input Intelligence implementation was fragmented across multiple modules (`document-intake`, `document-intelligence`, `document-evidence`, `raw-evidence`, `evidence-preservation`) with no unified architecture. Critical capabilities were missing, and the existing code had gaps that would have made the system unreliable for healthcare evidence extraction.

This rebuild creates a single, coherent `evidence-input` module that implements the complete architecture: **Input → Representation → Evidence → Normalized View**.

---

## What Existed Before

| Module | What It Did | What Was Missing |
|--------|-------------|------------------|
| `document-intake` | Document type tagging, boundary detection | No immutable storage, no evidence objects |
| `document-intelligence` | Extraction, structuring, inference separation | No negation handling, no attribution, no quality assessment |
| `document-evidence` | Basic source metadata recording | No provenance chain, no evidence objects |
| `raw-evidence` | Raw input persistence | No extraction, no normalization |
| `evidence-preservation` | Evidence chain for conclusions | Downstream-focused, not extraction-focused |

---

## What Was Rebuilt

### 1. Immutable Input Storage (`input-storage.ts`)

**Before:** Original file bytes were NOT preserved. Only extracted text hashes were stored.

**After:** Original bytes are stored immutably with SHA-256 hashes for integrity verification. The `ImmutableInput` type guarantees the original artifact is never modified.

**Why it matters:** Without immutable originals, there is no audit trail. If extraction fails or is questioned, the original evidence is lost.

### 2. Core Types (`types.ts`)

**Before:** Evidence-related types were scattered across multiple files with no single source of truth. No `EvidenceObject` type existed.

**After:** A single `types.ts` defines the complete type system:
- `ImmutableInput` — the original artifact
- `ParsedDocument` — structured representation with layout
- `EvidenceObject` — the irreducible evidence unit
- `NormalizedEvidence` — canonical view with original preserved
- `ProvenanceChain` — full audit trail
- `ConfidenceDimensions` — multi-dimensional confidence
- `EvidenceQuality` — source quality assessment

**Why it matters:** A unified type system prevents the previous fragmentation where evidence meant different things in different modules.

### 3. Negation & Attribution (`negation-attribution.ts`)

**Before:** No negation detection existed. "Patient has diabetes" and "Patient does not have diabetes" would be extracted identically. No attribution model existed.

**After:**
- `detectNegation()` — detects affirmed, negated, suspected, ruled_out, history_of, family_history_of, resolved
- `detectAttribution()` — detects who is making the statement (patient, caregiver, family, clinician)
- `createSourceLocation()` — preserves exact source location

**Why it matters:** In healthcare, negation is safety-critical. "No evidence of pneumonia" must NOT become "pneumonia" in the evidence store. "Daughter reports mother hasn't taken medication" is NOT the same as "Patient hasn't taken medication."

### 4. Multi-Dimensional Confidence (`confidence-quality.ts`)

**Before:** A single `overall` confidence score was used. No distinction between extraction confidence and evidence quality.

**After:**
- `ConfidenceDimensions` — six independent scores: ocr, parse, extraction, entity_normalization, temporal, negation_detection, overall
- `EvidenceQuality` — separate assessment of source reliability, completeness, legibility, directness, timeliness, specificity
- `verifyConfidenceQualitySeparation()` — guards against conflating the two

**Why it matters:** A perfectly extracted blurry caregiver note has HIGH extraction confidence but LOW evidence quality. These must not be confused. The system must know the difference between "I read this correctly" and "this source is reliable."

### 5. Evidence Object Factory (`evidence-object.ts`)

**Before:** No single `EvidenceObject` type existed. Evidence was represented as flat key-value pairs or domain-specific types.

**After:**
- `createEvidenceObject()` — creates the irreducible evidence object with ALL required fields
- `supersedeEvidenceObject()` — creates new versions without modifying old ones
- `validateEvidenceObject()` — validates all required fields are present

**Why it matters:** The evidence object is the core unit of the system. Every field is necessary — provenance, attribution, content, temporality, negation, confidence, quality. Remove any and critical information is lost.

### 6. Provenance Chain (`provenance.ts`)

**Before:** Source attribution was a single `document_id` reference. No full chain from input to evidence.

**After:**
- `createProvenanceChain()` — creates a full chain: original input → ingestion → parsing → extraction
- `verifyProvenanceChain()` — validates the chain is complete
- `formatProvenanceChain()` — human-readable audit trail

**Why it matters:** Every evidence object must answer "Show me exactly where this came from." The provenance chain provides this audit trail.

### 7. Duplicate Detection (`duplicate-detection.ts`)

**Before:** No duplicate detection existed. Repeated documentation would inflate evidence counts.

**After:**
- `computeEvidenceFingerprint()` — creates a hash for duplicate detection
- `detectDuplicates()` — identifies duplicate groups and classifies them
- Distinguishes: same file, same fact same source, same fact different sources, corroboration

**Why it matters:** Three documents stating the same fact does NOT mean three independent pieces of evidence. The system must distinguish between duplicate files, repeated statements, and genuine corroboration.

### 8. Normalization with Original Preservation (`normalization.ts`)

**Before:** Normalization was partial and not systematic. No guarantee that originals were preserved.

**After:**
- `normalizeMedication()` — maps to RxNorm with confidence
- `normalizeCondition()` — maps to ICD-10 with confidence
- `normalizeDate()` — maps to ISO 8601 with confidence
- `normalizeEvidence()` — full normalization preserving original
- `verifyNormalizationPreservation()` — validates originals are intact

**Why it matters:** Normalization is dangerous. "Tylenol" → "acetaminophen" is useful, but the original must always be recoverable. Normalization must be a VIEW on top of evidence, not a replacement.

### 9. Failure Mode Defenses (`failure-defenses.ts`)

**Before:** No systematic failure mode handling. Each module had ad-hoc checks.

**After:**
- 21 identified failure modes with specific defenses
- `validateAgainstFailureModes()` — validates evidence against all applicable defenses
- `createProcessingFailure()` — records failures with severity and affected evidence
- Automatic tracking of defense triggers

**Why it matters:** The system must know how it can fail and defend against each failure mode. OCR hallucination, wrong reading order, incorrect negation, wrong subject — each has a specific defense.

### 10. Main Pipeline (`pipeline.ts`)

**Before:** No unified pipeline. Processing was scattered across modules with no clear flow.

**After:**
- `ingestInput()` — Stage 1: Store immutable input
- `parseDocument()` — Stage 2: Parse into structured representation
- `extractEvidence()` — Stage 3: Extract evidence with full provenance
- `runEvidenceInputPipeline()` — Full pipeline with all safety mechanisms

**Why it matters:** A unified pipeline ensures every input goes through the same stages with all safety mechanisms active.

---

## What Improved

| Aspect | Before | After |
|--------|--------|-------|
| **Original preservation** | Only text hashes | Full immutable bytes with integrity verification |
| **Negation handling** | None | 7-status detection with confidence |
| **Attribution** | None | Speaker/author/subject with reporting type |
| **Confidence** | Single overall score | 6-dimensional independent scores |
| **Quality assessment** | None | 7-dimension quality model |
| **Provenance** | Single document reference | Full chain with transformation steps |
| **Duplicate detection** | None | Fingerprint-based with classification |
| **Normalization safety** | Partial | Additive-only with preservation verification |
| **Failure defenses** | Ad-hoc | 21 specific defenses with tracking |
| **Architecture** | Fragmented across 5+ modules | Single unified module |

---

## What Was Preserved from the Existing Codebase

The following existing modules remain in place and are NOT replaced by this rebuild:

- `document-intake/` — The tagging and classification logic is still used for initial document type detection
- `document-intelligence/` — The care-journey extraction and inference separation logic remains for downstream processing
- `raw-evidence/` — The raw evidence store remains for pre-extraction persistence
- `evidence-preservation/` — The evidence chain builder remains for downstream conclusion support

The new `evidence-input` module is designed to work alongside these existing modules, providing the foundational evidence layer that feeds into them.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    EVIDENCE & INPUT INTELLIGENCE                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────┐  │
│  │    INPUT      │    │  REPRESENTATION   │    │   EVIDENCE    │  │
│  │              │    │                  │    │               │  │
│  │ Immutable    │───▶│ Parsed Document  │───▶│ Evidence      │  │
│  │ Storage      │    │ with Layout      │    │ Object        │  │
│  │              │    │                  │    │               │  │
│  │ - bytes      │    │ - pages          │    │ - provenance  │  │
│  │ - hash       │    │ - regions        │    │ - attribution │  │
│  │ - metadata   │    │ - sections       │    │ - content     │  │
│  │              │    │ - reading order  │    │ - negation    │  │
│  └──────────────┘    └──────────────────┘    │ - confidence  │  │
│                                               │ - quality     │  │
│                                               └───────┬───────┘  │
│                                                       │          │
│                                                       ▼          │
│                                               ┌───────────────┐  │
│                                               │  NORMALIZED   │  │
│                                               │    VIEW       │  │
│                                               │               │  │
│                                               │ - canonical   │  │
│                                               │ - codes       │  │
│                                               │ - original    │  │
│                                               │   preserved   │  │
│                                               └───────────────┘  │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  CROSS-CUTTING CONCERNS:                                         │
│  • Negation & Attribution (safety-critical)                      │
│  • Multi-Dimensional Confidence (extraction accuracy)            │
│  • Evidence Quality (source reliability)                         │
│  • Provenance Chain (auditability)                               │
│  • Duplicate Detection (correctness)                             │
│  • Failure Defenses (trustworthiness)                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## The Irreducible Evidence Object

Every evidence object created by this system contains:

```
EvidenceObject
├── evidence_id           — Unique identifier
├── provenance            — Full chain from original input
│   ├── original_input_id
│   ├── ingestion_timestamp
│   ├── source_location (page, region, text span, section)
│   └── transformation_steps[]
├── attribution           — Who made this statement
│   ├── author
│   ├── speaker
│   ├── subject
│   └── reporting_type
├── content               — What is asserted
│   ├── type (medication, condition, etc.)
│   ├── value (structured)
│   └── original_text (verbatim)
├── temporality           — When
│   ├── temporal_status (present/past/future/unknown)
│   ├── event_date
│   └── date_confidence
├── negation              — Positive or negative
│   ├── negation_status (affirmed/negated/suspected/ruled_out/history_of/family_history_of/resolved)
│   └── certainty_level
├── confidence            — Multi-dimensional
│   ├── ocr_confidence
│   ├── parse_confidence
│   ├── extraction_confidence
│   ├── entity_normalization_confidence
│   ├── temporal_extraction_confidence
│   ├── negation_detection_confidence
│   └── overall_confidence
├── quality               — Source reliability
│   ├── quality_score (high/medium/low)
│   ├── source_type_reliability
│   ├── completeness
│   ├── legibility
│   ├── directness
│   ├── timeliness
│   └── specificity
└── metadata              — Processing info
    ├── extraction_timestamp
    ├── extraction_model_version
    ├── superseded_by
    └── is_current
```

Every field is necessary. Remove any and critical information is lost.

---

## Boundary Definition

**This module OWNS:**
- Original input preservation
- Input parsing and representation
- Evidence extraction from parsed content
- Evidence attribution and provenance
- Extraction confidence and quality metadata
- Normalization with original preservation
- Duplicate detection
- Failure mode defenses

**This module does NOT own:**
- Clinical interpretation of evidence
- Inference beyond what is stated
- Assessment of source truthfulness
- Temporal reasoning across documents
- Reconciliation of conflicting evidence
- Downstream state construction

**The hard boundary:** Evidence layer preserves source assertions. Interpretation layer assigns clinical meaning. Never the twain shall meet.
