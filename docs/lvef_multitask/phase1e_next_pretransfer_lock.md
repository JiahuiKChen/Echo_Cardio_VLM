# Next bounded phase: live quota and pre-transfer lock

Status: proposed owner-review phase only. This document does not authorize a DICOM body transfer or full C3.

## Objective

Convert the passed current-source inventory, Autoclass, cost, and 2-TB planning authorities into a contemporaneous pre-transfer decision. The phase should verify live quota activation and current usage, seal the backup/migration and production-command authorities, and return a later full-C3 authorization packet without executing it.

The requester-pays low/base/high estimates of $136.101850/$142.906680/$171.488015 and the owner-provided SCC storage estimate are already accepted for planning. Phase 1E-D did not investigate, recalculate, or independently reverify them. This acceptance does not authorize the DICOM transfer. The live SCC quota report subsequently showed only 989 GB on the research tier, so the additional one-terabyte allocation is required before the first body request.

## Permitted scope

- revalidate the six immutable job-7104307 outputs and six supplemental attempt-002 aggregates;
- read current SCC filesystem, `pquota`, mount, and integer-byte usage evidence;
- finish the checksum-bound disaster-tier migration/backup witness without moving or deleting data unless separately authorized;
- reconcile the existing 2-TB resource calculation against live quota and usage;
- preserve the frozen owner cost disposition without recalculation;
- finish and smoke-test production download/extraction/embedding orchestration without cloud media access;
- freeze command, config, checkpoint, environment, source, resume, preservation, and safety checksums;
- prepare—but do not execute—the exact full-C3 authorization block.

## Hard stops

No GCS body or media request, DICOM transfer, `alt=media`, object listing, storage audit repetition, quota mutation, file migration/deletion, extraction, EchoPrime inference, embedding generation, model fitting, prediction generation, confirmatory-performance access, Section 5, or full-C3 submission is permitted without a new explicit owner authorization.

## Required live quota proof

The current plan projects 1,611,642,076,332 peak bytes and requires 200,000,000,000 free bytes. The live effective research quota must therefore be at least 1,811,642,076,332 bytes; the preferred nominal allocation is 2,000,000,000,000 bytes. Evidence must include contemporaneous `pquota`, filesystem identity and unit interpretation, and integer-byte `/restricted/projectnb/mimicecho` usage. It must distinguish bytes already included in current usage from bytes still planned for migration and must not double count them.

## Decision boundary

The cost-planning gate is closed as `OWNER_ACCEPTED_FOR_PLANNING`, including the $171.488015 high requester-pays scenario and owner-provided SCC storage estimate. Phase 1E-D records `NO_GO`: the current 989,000,000,000-byte research quota is below the 1,811,642,076,332-byte minimum effective quota, backup/migration completion is unverified, and the production downloader/batch/finalizer implementations are absent. A later phase may recommend full-C3 authorization only after those gates pass and all command/config/source/checkpoint/environment authorities are checksum-frozen. A separate explicit owner authorization for the first DICOM transfer remains mandatory.
