# C3 Autoclass evidence and adjudication plan

Status: implementation authority for one supplemental metadata-only attempt; not full-C3 authorization.

## Problem and governing distinction

The completed job-7104307 parser explicitly requested Autoclass fields but reduced an absent key and several nonmapping JSON states to the same pair of Booleans. The downstream validator then required the Autoclass mapping to be present and disabled. This made an otherwise complete source inventory depend on a pricing/configuration field and prevented the existing derived Boolean from proving the original JSON shape.

The repair preserves two independent closed states. The raw state records key absence, JSON null, explicit Boolean mappings, empty or incomplete mappings, malformed values, or an unproven request/receipt. The effective state records explicit enablement or disablement, documented default-disabled absence, unresolved null/incomplete states, or malformed/unproven evidence. JSON null, empty or incomplete mappings, malformed content, and missing receipt authority are never treated as key absence.

## Primary Google authorities

The following primary sources were independently retrieved on 2026-08-09:

- [Buckets | Cloud Storage | Google Cloud Documentation](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets), Cloud Storage JSON API v1 Bucket resource, document updated 2026-07-31. It directly supports interpreting an unset `autoclass.enabled` value as disabled.
- [Package google.storage.v2 | Cloud Storage | Google Cloud Documentation](https://docs.cloud.google.com/storage/docs/reference/rpc/google.storage.v2), Cloud Storage API v2 Bucket resource, document updated 2026-07-20. It directly supports that absent Autoclass configuration disables Autoclass and leaves the bucket unaffected.
- [Storage pricing | Google Cloud](https://cloud.google.com/storage/pricing), current operation, retrieval, network-transfer, unit, and Always Free pricing. It classifies `objects.list` as Class A and storage GET operations, including bucket and object GETs, as Class B; Standard retrieval is zero.
- [Requester Pays | Cloud Storage | Google Cloud Documentation](https://docs.cloud.google.com/storage/docs/requester-pays), Requester Pays semantics, document updated 2026-07-10.
- [Buckets: get | Cloud Storage | Google Cloud Documentation](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/get), JSON API v1 metadata-only method context.
- [Objects: list | Cloud Storage | Google Cloud Documentation](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/list), JSON API v1 paginated metadata-list context, document updated 2026-07-22.

No conflicting primary Google source was found. These semantics may be applied only after the owner-private response receipt proves a successful, explicitly projected, nonredirected JSON `buckets.get` response and binds its exact raw bytes.

## Supplemental attempt architecture

`phase1ebc_autoclass_adjudication_attempt_001` failed before token acquisition or network access because the requester-pays environment value was consumed before the Section 3D receipt validator independently reconstructed its authority. Its owner-private partial evidence remains preserved and is not reused or overwritten.

`phase1ebc_autoclass_adjudication_attempt_002` binds the six immutable job-7104307 outputs, one owner-private request receipt, one owner-private exact raw response, one owner-private response receipt, the primary-source registry, corrected code and policy hashes, and six aggregate-safe products. Creation is exclusive and no-clobber. The repair keeps the requester-pays binding available through receipt validation and token acquisition, removes it immediately after both checks even on failure, and never exposes it in argv or output. The pre-request receipt is written before network access, making either attempt non-rerunnable after a partial failure.

The one live operation is a no-retry JSON API v1 `buckets.get` with an explicit minimum projection. It prohibits redirects, media endpoints, object listing, object GET, BigQuery, object-body reads, and any cloud identifier or credential export. The immutable 526-page listing and storage audit are not repeated.

## Independent authority gates

Source-inventory authority depends on the exact cohort, checksum-bound manifests and split map, selected-object reconciliation, exact bytes, batch reconciliation, zero missing/unexpected/conflicting objects, zero media/body access, and the original aggregate safety gate. It does not depend on Autoclass or pricing. Its maximum claim is `PASS_CURRENT_SELECTED_SOURCE_INVENTORY_FOR_PROSPECTIVE_C3`.

Historical object-byte identity remains `NOT_ESTABLISHED_NO_HISTORICAL_COMPARATORS` because the historical size, MD5, CRC32C, and generation comparator denominators were all zero.

Cost authority separately requires verified location/rate-region mapping, Requester Pays, all selected objects currently in Standard storage, an authoritatively disabled Autoclass state, exact bytes and operation counts, primary current rates, and explicit uncertainty. Full C3 remains unauthorized even if both supplemental authorities pass.

## Cost correction fixed before live evidence

The immutable estimate classified its one bucket metadata GET as Class A. Current primary pricing classifies it as Class B, and the supplemental attempt adds one additional Class B bucket GET. No other numeric input changes. Reconstructed from the immutable job inputs and original formula, the original-formula low/base/high values are $136.101859, $142.906689, and $171.488027. Low is one transfer pass plus its operations; base adds a 5% retry reserve to transfer, body-GET, and retrieval costs; high adds 20% general contingency to base. The corrected values are $136.101850, $142.906680, and $171.488015. The underlying low/base correction is -$0.0000092 and its high-scenario propagation is -$0.00001104; six-decimal aggregate values are independently rounded, so subtracting displayed rounded totals can differ by one micro-dollar. The estimate assumes US multi-region to US internet transfer in the first 10-TiB monthly tier, one body GET per selected object, and currently Standard storage. It excludes free-tier deductions, taxes/currency conversion, SCC-side costs, unrelated account traffic, and retries beyond the stated reserve. It is a planning estimate, not an invoice guarantee, and may be described only as appearing below owner-reported available trial credit; the credit is not independent billing authority.

## Hard stop

This phase does not authorize Section 4, qsub, another object listing, storage re-audit, object bodies, DICOM transfer, Section 5, extraction, EchoPrime inference, embeddings, models, predictions, confirmatory performance, quota changes, or cloud control-plane changes.
