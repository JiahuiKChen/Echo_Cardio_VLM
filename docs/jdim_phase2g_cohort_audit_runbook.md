# JDIM Phase 2G Cohort and Audit Preparation

This phase consumes the locked Phase 2E corrected outputs. It does not run
aggregation, model fitting, predictions, bootstraps, calibration, threshold
metrics, or corrected/original comparisons.

## Required gates

1. `validate_jdim_phase2g_sources.py` verifies the authorized commit, clean
   worktree, canonical certificate and blocker hashes, duplicate packets, and
   corrected clip/study artifacts. The proposed output root must not exist.
2. `scc_run_jdim_phase2g.sh preflight` regenerates path-free lineage metadata
   in `/tmp` and traverses the complete cohort/output policy without writing
   cohort artifacts.
3. `scc_run_jdim_phase2g.sh run` creates the immutable output root only after
   those gates pass.

## Cohort policy

- The metadata unions may contain only the hash-pinned 32 adjudicated groups:
  64 member rows, two rows per group, all from `batch_000`, affecting one
  study and one subject.
- Raw row counts and adjudicated unique row counts are both retained.
- `legacy_stage_d_500` is accepted outside the selected universe only through
  its exact declared study-set hashes. Any unknown duplicate or outside study
  fails closed.
- The final manifest must equal the canonical-universe study set union the
  exact declared legacy outside-universe set after study-level deduplication.

## Stage statuses

- Cohort: `JDIM_COHORT_FLOW_LOCKED`
- Roster: `AUDIT_ROSTER_LOCKED`
- No reconstructable locked-roster study: `BLOCKED_AUDIT_SOURCE_RESTORATION`
- Pilot reconstruction mismatch: `BLOCKED_AUDIT_RECONSTRUCTION`
- Successful technical pilot: `AUDIT_PACKET_READY_FOR_HUMAN_REVIEW`

A source or pilot blocker does not invalidate a locked cohort or roster. The
locked roster is never replaced with convenience studies. The pilot records no
clinical-content findings, uses no OCR, and is excluded from prevalence
estimation.
