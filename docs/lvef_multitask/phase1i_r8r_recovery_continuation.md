# Phase 1I-R8R recovery and continuation

R8R preserves scientific authority at commit
`e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed`, attempt
`lvef_c3_full_904d0ab65f003c1e_e1cdb674`, and plan SHA-256
`904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247`.
The repair commit is implementation authority only.

The Batch-3 preservation failure was a replay-boundary inconsistency. One
approved technical-disposition row caused pandas to serialize integer-valued
successful-row count columns as `N.0`; the preservation CSV reader had kept
those tokens as strings. The boundary now losslessly accepts only `N`, `N.0`,
or an allowed empty value and leaves all v2 eligibility, artifact-partition,
integrity, and affected-study coverage rules unchanged. Nested preservation
errors retain a closed validation-substage and their exact safe code.

Runtime validation now has two closed contexts. `LIVE_RUNTIME_CAPTURE` remains
strict at submission. `SEALED_SCHEDULER_RUNTIME_REPLAY` requires the exact
environment receipt and all portable Python, package, torch, torchvision,
CUDA, cuDNN, CRC32C, checkpoint, Git, and qsub bindings while excluding only
the descriptive node-local operating-system string.

The fixed R8R controller has no caller-selectable attempt, plan, batch, range,
or path. Its CPU recovery invokes only existing preservation, cache-retirement,
and finalization stages for `c3_batch_002`. A sealed download-manifest and
nofollow metadata projection provide raw-DICOM authority without reading a
DICOM body; retained NPZ and embedding bodies remain subject to the ordinary
preservation checks. Recovery controls and terminal evidence are no-clobber
and single-use.

After a valid three-batch prefix, one fresh capacity observation derives demand
only from plan tasks 4--19 and requires independent 200 GB quota and physical
reserves plus remaining file slots. The continuation topology is fixed to one
array (`4-19`, `tc=1`) and one held CPU finalizer. A dedicated finalizer policy
accepts exactly two implementation epochs: immutable original receipts for
Batches 1--2 and the repair implementation for Batches 3--19. The ordinary
single-epoch finalizer policy is unchanged.

No recovery path authorizes cloud access, DICOM extraction, EchoPrime,
embedding generation, GPU execution, model fitting, prediction generation, or
confirmatory-performance access.
