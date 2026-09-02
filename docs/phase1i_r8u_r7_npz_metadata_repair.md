# Phase 1I R8U-R7 body-free NPZ metadata repair

R8U-R7 fixes the Batch-16 preservation validator without creating another
scientific or publication epoch.  The former body-free check compared two raw
`os.stat_result` objects even though its persisted seal excluded access time.
The replacement compares one closed projection: device, inode, full mode
(file type plus permissions), UID, GID, link count, size, modification time,
and change time.  Access time is counted only as an aggregate diagnostic.

The manifest-driven pass seals the exact 10,187 producer-relative paths once,
including the producer's stage-root-plus-manifest `clips/clips/...` topology;
the tree walk proves exact path-set closure, and the later preservation pass
compares each leaf with its sealed projection.  Extracted NPZ bodies remain unopened;
the existing clip- and study-embedding stores remain readable control
artifacts.  Missing, additional, substituted, nonregular, wrong-owner,
wrong-mode, multi-link, empty, cross-device, or stable-metadata changes remain
blocking with field-specific safe codes.

The fixed `r8u_r7_batch16_preservation_recovery` entrypoint is CPU-only and
can run only preservation, cache-retirement authorization, canonical cache
retirement, final-ledger creation, and Batch-16 finalization.  It reuses and
hash-binds the completed R8U-R6 publication, extraction/pooling ledgers, and
10,187 clip/250 study embeddings, as well as the immutable Batches 1–15
receipts and retained 4,757-file historical partial cache.  Its capacity gate
covers only bounded control artifacts and does not recharge completed science.

Only a terminal R8U-R7 PASS plus one successful accounting readback permits
the fixed Tasks 17–19 continuation: one `17-19` GPU array with concurrency one
and one CPU cohort finalizer held on that array.  No arbitrary attempt, batch,
path, job, retry, or task-range input is exposed.
