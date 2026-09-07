# Phase 1I-R8U-R7G-R1 historical JSON compatibility

R7G-R1 is a serialization-only control-plane repair for retrospective
adjudication of the completed R7F Jobs 7480830 and 7480831. It does not alter
cohort selection, the immutable plan, extraction, EchoPrime, embeddings,
technical dispositions, preservation, cache retirement, or any other
scientific method.

## Closed JSON policy

Every role in the R7G-R1 serialization audit is assigned to exactly one
closed policy; callers cannot select a weaker policy dynamically.

1. `HASH_PINNED_HISTORICAL_PRODUCER_JSON` requires a stable owner-private,
   no-follow read; a mandatory exact SHA-256 over the original bytes; strict
   UTF-8 JSON with duplicate-key and nonfinite-value rejection; the required
   top-level type; and the producer's schema and semantic validator. It does
   not compare historical bytes with R7G's compact serializer. The fixed R7F
   capacity receipt therefore remains valid in its producer's indented format
   only when its pinned byte hash and capacity semantics both pass.
2. `PRODUCER_CANONICAL_JSON` performs the same stable strict parsing and then
   enforces the artifact's declared producer serializer plus its producer
   schema and semantic validation.
3. `R7G_COMPACT_CANONICAL_JSON` is reserved for new R7G artifacts. Reopened
   bytes must equal `core.canonical_json_bytes` exactly and must pass the R7G
   schema and semantic validator.

The closed maximum registry contains 35 top-level receipt role instances:

| Policy | Role instances | Count |
| --- | --- | ---: |
| Hash-pinned historical producer JSON | five fixed R7F authority receipts and the 16 prefix batch-finalization receipts | 21 |
| Producer-canonical JSON | scheduler-account authority, two CPU-probe receipts, three tail batch-finalization receipts, and the optional original full-cohort receipt | 7 |
| R7G compact-canonical JSON | terminal authority, four fixed accounting receipts, cohort-finalization receipt, and post-reconstruction lock receipt | 7 |
| **Closed maximum** | optional roles remain registered even when their artifact is absent | **35** |

This is an artifact-instance count, not a count of schema families or files
present at one moment. The optional historical cohort role remains assigned
to its producer policy when no receipt exists. The immutable plan is validated
independently by both its raw and canonical SHA-256 authority and is not a
receipt instance in this count. Nested batch evidence retains its existing
producer-specific validators.

For the producer-canonical batch/cohort roles, Batch 17--19 receipts use the
orchestration core's sorted compact ASCII JSON with no trailing newline. The
historical full-cohort finalizer uses sorted, two-space-indented UTF-8 JSON
with one trailing newline. R7G-R1 checks those distinct byte contracts rather
than substituting its own serializer.

The two probe-receipt validators also replay their embedded digests against
the fixed probe-authority, worker-context, probe-accounting, and raw qsub
evidence bytes. Those byte dependencies are not reopened as caller-selectable
JSON roles and therefore do not expand the closed 35-role reader registry.

## Commit authority separation

The completed jobs remain bound to R7F runtime implementation commit
`2223d9768a1cc23efbe95a3c5474ea747a383a10`. The unchanged R7G base
adjudicator is commit `4dc4b2327f91ffd3912c91a7113f16d41d0562a8`.
The R7G-R1 adjudicator is the single direct child of that base (the commit
containing this repair); its full SHA is resolved from the synchronized
tracked HEAD and bound into new receipts. Historical R7F artifacts are not
required to name either adjudication commit. The scientific authority remains
unchanged at `e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed`.

## Read-only authority preflight

After local, origin, and SCC synchronize at R7G-R1, run the preflight before
terminal-authority creation or qacct:

```text
python3 scripts/lvef_c3_r8u_r7g_terminal_adjudicator.py \
  --preflight-fixed-r8u-r7f-authorities
```

It reopens the immutable plan and all fixed R7F authority inputs under their
assigned policies, verifies pinned hashes and producer validators, and tests
the compact R7G output machinery synthetically. It creates no authority or
accounting receipt, performs no qacct or qsub, and reads no DICOM or NPZ body.
Adjudication may proceed only after the exact marker
`PASS_R7G_R1_ALL_FIXED_AUTHORITY_INPUTS`.
