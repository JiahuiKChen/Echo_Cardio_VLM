# Phase 1I-R5E: bounded technical dispositions and raw-copy retirement

R5E extends, but does not reinterpret, the locked
`source_signal_object_technical_disposition_v1` row-level policy. Before
frozen-encoder inference, multiframe cines underwent prespecified,
outcome-independent automated technical quality control. Rare cines failing
the locked source-signal criterion were assigned an explicit technical
disposition and were not embedded. A batch could pass with technical
dispositions only when there were no integrity failures, no more than 10
dispositions and no more than 0.1% of multiframe candidates, and every affected
study retained at least one successfully embedded cine. Candidate,
disposition, and embedding counts were reconciled exactly. The limit is an
owner-specified operational guardrail, not a clinical threshold.

The only eligible substage remains `SOURCE_SIGNAL_QUALITY_FAILURE`. Decode,
color, crop/resize, temporal sampling, output-write, integrity, storage,
scheduler, cloud, GPU, and embedding failures remain blocking. The decision
does not consume labels, outcomes, split roles, predictions, or performance.
Disposed cines retain explicit accounting and have no NPZ, embedding,
placeholder, or substitute.

Claims, manifests, receipts, checksums, ledgers, logs, embeddings, and
diagnostic evidence were retained. Redundant raw DICOM copies from a
noncanonical terminal failed attempt were retired only after exact
object-authority concordance with a fully retained later attempt. The
canonical source dataset and scientific denominator were unchanged. The
retirement is limited to the two internally fixed Batch-1/Batch-2 raw-object
leaf directories; the older NPZ cache and the complete R4 attempt remain
retained.

Capacity admission uses current quota, current usage, current physical
availability, and current file slots. Allocation visibility is reported
separately and does not override an actual headroom pass. A fresh claim remains
PASS-only and binds the exact dynamic capacity receipt plus either the exact
raw-retirement receipt or the cleanup-skipped authority token. No prior batch,
NPZ, or embedding is adopted into the fresh successor.
