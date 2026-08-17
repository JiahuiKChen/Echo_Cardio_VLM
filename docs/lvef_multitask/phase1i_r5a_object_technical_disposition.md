# Phase 1I R5A object technical disposition

Phase 1I R5A adds a closed, versioned technical-disposition policy for a source
object that cannot produce a usable cine under the frozen preprocessor. The sole
eligible class is
`SOURCE_SIGNAL_QUALITY_UNUSABLE_UNDER_FROZEN_PREPROCESSOR`; decode, color,
crop, sampling, output-write, authority, and schema failures remain blocking.
Classification requires the reviewed plan, selected-source, download, DICOM,
raw-retention, artifact-absence, and study-coverage authorities. It does not
receive labels, outcomes, targets, predictions, or performance.

Production accounting now partitions every requested multiframe object exactly
once into successful extraction or approved technical disposition. EchoPrime
consumes successful extractions only. Restricted technical-disposition manifests
and their hashes are replayed through batch preservation, NPZ-only retirement,
and cohort finalization. The aggregate-safe final schema reports counts, closed
gates, policy identity, and a manifest-set hash without source or study
identifiers.

Fresh production uses immutable-plan v3 and preservation/finalization receipt
v3. Exact v2 compatibility remains confined to bounded historical replay and
legacy canary evidence. Zero-disposition batches retain the pre-existing
scientific array and pooling behavior and emit the exact one-class mapping with
count zero. This record describes implementation authority only; it does not
claim live SCC execution, model fitting, or performance evaluation.
