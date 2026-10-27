# Task 5 — API route verification (2026-10-23)

## Changes

- Mount POST /api/extract in FastAPI, require a valid server-issued anonymous-session bearer capability, enforce a 10 MiB limit, support UTF-8 text and text-based PDFs, and return explicit JSON errors for unsupported formats.
- Mount POST /api/track and persist registered operational events in additive solen_events storage. Hash user/session identifiers and whitelist metadata fields; free-text error messages and unapproved fields are discarded.
- Add migration 20261023_06 after the current merge head.
- Add route contract tests for auth, methods, JSON response shape, extractor limits/unsupported image behavior, event validation, and telemetry metadata minimization.
- Preserve the canonical /api/situation route and the existing /api/v1/* scoped APIs.

## Supported/unsupported boundaries

- PDF extraction reads embedded text only. Scanned PDFs and images require OCR, which is not claimed available until Tesseract and its language data are verified in the actual hosting environment.
- Upload, Scan and Snap are UI entry modes; they are not separate HTTP routes. The active pipeline is /api/extract then /api/situation.
- Office documents and image OCR currently return an explicit 415 response instead of fabricated text or a false success.
- No live Railway migration or smoke test is claimed. The deployment service has previously been reported unavailable.