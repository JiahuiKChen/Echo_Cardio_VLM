# Next bounded phase: live quota and pre-transfer lock

Status: proposed owner-review phase only. This document does not authorize a DICOM body transfer or full C3.

## Objective

Convert the passed current-source inventory, Autoclass, cost, and 2-TB planning authorities into a contemporaneous pre-transfer decision. The phase should verify live quota activation and current usage, seal the backup/migration and production-command authorities, and return a later full-C3 authorization packet without executing it.

## Permitted scope

- revalidate the six immutable job-7104307 outputs and six supplemental attempt-002 aggregates;
- read current SCC filesystem, `pquota`, mount, and integer-byte usage evidence;
- finish the checksum-bound disaster-tier migration/backup witness without moving or deleting data unless separately authorized;
- reconcile the existing 2-TB resource calculation against live quota and usage;
- record owner budget disposition for the $171.488015 high requester-pays planning estimate and the SCC quota quote;
- finish and smoke-test production download/extraction/embedding orchestration without cloud media access;
- freeze command, config, checkpoint, environment, source, resume, preservation, and safety checksums;
- prepare—but do not execute—the exact full-C3 authorization block.

## Hard stops

No GCS body or media request, DICOM transfer, `alt=media`, object listing, storage audit repetition, quota mutation, file migration/deletion, extraction, EchoPrime inference, embedding generation, model fitting, prediction generation, confirmatory-performance access, Section 5, or full-C3 submission is permitted without a new explicit owner authorization.

## Required live quota proof

The current plan projects 1,611,642,076,332 peak bytes and requires 200,000,000,000 free bytes. The live effective research quota must therefore be at least 1,811,642,076,332 bytes; the preferred nominal allocation is 2,000,000,000,000 bytes. Evidence must include contemporaneous `pquota`, filesystem identity and unit interpretation, and integer-byte `/restricted/projectnb/mimicecho` usage. It must distinguish bytes already included in current usage from bytes still planned for migration and must not double count them.

## Decision boundary

The phase may recommend a later full-C3 authorization only if the live resource/headroom calculation passes, backup/migration authority passes, the requester-pays and SCC budgets are owner-approved, the production orchestration and recovery tests pass, and all command/config/source/checkpoint/environment authorities are checksum-frozen. It may not itself start the reconstruction.
