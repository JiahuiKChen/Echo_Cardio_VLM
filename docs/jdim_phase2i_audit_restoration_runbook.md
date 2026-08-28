# JDIM Phase 2I Restricted Runbook

This runbook covers only the locked-roster source restoration, technical evidence-tier assignment, and blinded human-review interface for JDIM-D-26-02840.

## Invariants

- Use the committed Phase 2I source SHA and a clean SCC checkout.
- Verify the canonical corrected-output certificate, cohort lock, audit-roster lock, sampling design, clip roster, restoration manifest, and prior 18-row pilot by their pinned hashes.
- Never regenerate the roster, rerun a model, inspect target values, run OCR, or create clinical-content annotations in Jobs A or B.
- Keep DICOMs, processed inputs, row-level linkage, media, and checkpoints in restricted storage outside Git.

## Job A

Run the wrapper in `preflight` mode before creating an SCC output root. The run stage first diagnoses exactly the 18 retained pilot clips. Restoration proceeds only for `REPLAY_PATH_VALIDATED` or `REPLAY_USABLE_WITH_TIERED_REPORTING`.

The restoration request contains only official MIMIC-IV-ECHO v1.0 `files/` paths from the locked roster. Downloads are resumable, use a mode-0600 `$HOME/.netrc`, validate DICOM structure, preserve local SHA-256 values, and never overwrite an existing file. If authentication is absent, Job A returns `BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION` and writes a restricted action sheet.

Technical classification is conservative:

- `EXACT_MODEL_INPUT`: retained historical model input or bitwise replay.
- `VERIFIED_EQUIVALENT_REPLAY`: all equivalence safeguards established.
- `SOURCE_ACQUISITION_ONLY`: source is uniquely linked and viewable, but model-input equivalence is unverified.
- `NOT_ASSESSABLE`: source or provenance is inadequate.

## Job B

Submit Job B only after Job A reports `AUDIT_INPUTS_TECHNICALLY_LOCKED`. Job B renders opaque-token media and creates the localhost-only interface. The reader manifests expose only opaque audit IDs, clip IDs, review order, evidence tier, and opaque media tokens.

Serve the interface through an SCC tunnel with `scripts/serve_jdim_audit_interface.py`. Use different reader IDs and roles for the primary and second readers. Checkpoints are atomic, role-separated, and immutable after lock. There is no OCR, automated content classification, screenshot button, download button, or unrestricted export route.

## Human Workflow

1. Primary reader reviews all locked studies and clips, then locks the primary checkpoint.
2. Second reader independently reviews only the locked second-reader subset and locks that checkpoint.
3. Adjudicate positives, uncertain findings, discordances, and candidate-value findings while still blinded.
4. Only after all blinded records are locked may the team unblind target membership and compare adjudicated candidate values with structured report-label values.
5. Run the separately gated aggregation and manuscript-insertion phase; do not infer cohort-wide absence from zero findings.
