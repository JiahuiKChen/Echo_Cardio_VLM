# C3 Autoclass, source-inventory, and cost adjudication

Status: **supplemental adjudication passed; full C3 remains unauthorized pending owner review and pre-transfer gates**.

## Governing authorities

- starting commit: `223eed3bfc9566eea818425e69e74ca1c8960b5f`;
- final executable implementation commit: `515f8e5760e62bc46d8ae85c7873ec11203490e4`;
- immutable source evidence: Section 4 scheduler job `7104307` and its six original aggregate outputs;
- completed no-clobber attempt: `phase1ebc_autoclass_adjudication_attempt_002`;
- failed no-clobber attempt retained: `phase1ebc_autoclass_adjudication_attempt_001`.

Attempt 001 wrote only its owner-private pre-request receipt. It failed before token acquisition or network access because the requester-pays authority was consumed before the existing authority validator reconstructed its expected environment. The repair keeps that binding available through both receipt validation and token acquisition, removes it immediately afterward even on failure, and passes full-sequence regression tests. Attempt 001 remains preserved and was neither deleted nor reused.

## Prior contradiction and semantic ruling

The original parser explicitly requested Autoclass metadata but reduced key absence and multiple nonmapping raw states to the same two Booleans. The original validator then required the Autoclass mapping to be present and disabled. Source-inventory and cost authority were therefore coupled to a pricing/configuration field that the preserved derived Booleans could not prove.

```text
CURRENT_AUTOCLASS_PARSER_BEHAVIOR_CONFIRMED=YES
CURRENT_AUTOCLASS_VALIDATOR_CONTRADICTION_CONFIRMED=YES
SOURCE_AND_COST_AUTHORITY_CURRENTLY_COUPLED=YES
```

The corrected data model preserves the raw observation independently from the effective semantic state. The one successful JSON API v1 `buckets.get` returned a 199-byte JSON response under the exact projected-field and no-redirect contract. The owner-private raw response receipt proves that the `autoclass` key was absent. Applying the primary Google default rule yields:

```text
RAW_AUTOCLASS_OBSERVATION_STATE=KEY_ABSENT
AUTOCLASS_EFFECTIVE_STATE=ABSENT_CONFIGURATION_DEFAULT_DISABLED
AUTOCLASS_DISABLED_AUTHORITATIVELY=YES
```

No official primary source conflicting with that default-disabled interpretation was found.

## Primary Google evidence

Sources were independently retrieved on 2026-08-09:

| Source | API/version context | Adjudicated support |
|---|---|---|
| [Buckets, Cloud Storage JSON API](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets) | JSON API v1 Bucket resource; document updated 2026-07-31 | Direct: unset `autoclass.enabled` means disabled |
| [Package google.storage.v2](https://docs.cloud.google.com/storage/docs/reference/rpc/google.storage.v2) | Cloud Storage API v2 Bucket resource; document updated 2026-07-20 | Direct: no Autoclass configuration means disabled and no bucket effect |
| [Buckets: get](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/get) | JSON API v1 metadata method; document updated 2026-07-29 | Direct method authority for the targeted metadata receipt |
| [Objects: list](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/list) | JSON API v1 paginated metadata listing; document updated 2026-07-22 | Direct method authority for the immutable 526-page listing |
| [Cloud Storage pricing](https://cloud.google.com/storage/pricing) | Current operation, retrieval, and transfer rates | Direct: `objects.list` is Class A; bucket/object GET operations are Class B; Standard retrieval is zero |
| [Requester Pays](https://docs.cloud.google.com/storage/docs/requester-pays) | Cloud Storage billing semantics; document updated 2026-07-10 | Direct requester-pays authority and billing-responsibility boundary |

## Immutable source outputs

All six job-7104307 files retained their original byte sizes and SHA-256 values after both implementation and execution. They were not overwritten or regenerated.

| File | Bytes | SHA-256 |
|---|---:|---|
| `scc_storage_inventory.summary.json` | 2,566 | `3e27c71285558402d546bd7e15290cbd08fbb5b1eea445d2d04bc5cc9d8df3d6` |
| `c3_full_source_preflight.summary.json` | 2,866 | `8aaac6cbd62245184db05d47a98cd69ca5787a8caaec620e9a410ad99d0694b6` |
| `c3_full_source_preflight_by_batch.csv` | 1,136 | `6c17d2bccf992d79023023e011431fe86da35cbab68451f7fb3ea85520abbfa1` |
| `c3_full_source_cost_estimate.json` | 1,659 | `bad47492ca5980b91b9560c5cd385afd87a4f54b48e1a6a3fe149ae04be03285` |
| `c3_full_source_preflight_safety_gate.json` | 609 | `d846b8d6210b50e20533bac3361c42ff5bde2bd24360ac18f6178c4c0b671c54` |
| `c3_full_resource_plan.json` | 5,261 | `5610bd3ec3a2cf3fd4ab905946ab6f3ab25824e38d30ced1b8061f349ba3b357` |

## Supplemental aggregate products

Each file below passed its exact closed safe-export schema. The exact raw response and request/response receipts remain owner-private, mode 600, and outside Git.

| File | Bytes | SHA-256 | Schema |
|---|---:|---|---|
| `c3_autoclass_adjudication.summary.json` | 1,604 | `ec480c69e3a2412b18c958b43bc361db55fd9faf16dcf3ed8b25256e5ba21f1a` | PASS |
| `c3_source_inventory_authority_adjudication.summary.json` | 1,588 | `3cce1ef791ee20b9528f38c001d4fbb5405354c20053d083502138de9647c5dd` | PASS |
| `c3_cost_authority_adjudication.summary.json` | 3,456 | `3ac53f0aa7afbd3cd1195c78b467bb11041c87f9dc89d8ba0d64fd311c25dc56` | PASS |
| `c3_autoclass_adjudication_provenance_manifest.json` | 6,295 | `4ab11f7c255efd192cebc612704e11ae5b855d8f3fb8710b2261f768d522b11b` | PASS |
| `c3_autoclass_adjudication_safety_gate.json` | 1,151 | `7718e5f7aeb718f1b68973bae1bd27c3a7645d39e04414d218c616c64528b363` | PASS |
| `c3_autoclass_combined_validation.summary.json` | 1,173 | `ec64010819b001f25147c74b8e73af9673b1525acb91ef4355414a34f3f6e73d` | PASS |

## Independent rulings

Source inventory passes as `PASS_CURRENT_SELECTED_SOURCE_INVENTORY_FOR_PROSPECTIVE_C3`: 4,530 selected studies and subjects; 335,984 requested and verified objects; 1,216,569,133,322 exact bytes; and zero missing objects, unexpected selected objects, or ownership conflicts. Autoclass and pricing are not source-inventory gates.

This is current public-source inventory authority, not proof that current objects are byte-identical to historical downloads. Historical size, MD5, CRC32C, and generation comparator denominators were zero, so the preserved limitation is `NOT_ESTABLISHED_NO_HISTORICAL_COMPARATORS`.

Cost authority passes as `PASS_RATE_EXPLICIT_PLANNING_ESTIMATE`. The original estimate incorrectly classified its one bucket GET as Class A. The corrected estimate classifies that operation as Class B and includes the supplemental targeted GET as a second Class B operation. Source bytes, 526 listing pages, 335,984 future body GETs, retrieval, retry, and contingency inputs are unchanged.

| Scenario | Reconstructed original formula | Corrected estimate | Six-decimal rounding of exact unrounded change |
|---|---:|---:|---:|
| Low | $136.101859 | $136.101850 | -$0.000009 |
| Base | $142.906689 | $142.906680 | -$0.000009 |
| High | $171.488027 | $171.488015 | -$0.000011 |

The exact unrounded low/base correction is -$0.0000092; 20% propagation gives -$0.00001104 for high. Independent six-decimal rounding explains the displayed micro-dollar differences. These are planning estimates, not invoice guarantees. They appear below the owner-reported trial credit; that credit was not itself treated as independent billing authority or budget approval.

The owner subsequently froze the following project-stage disposition:

```text
COST_ESTIMATE_DISPOSITION=OWNER_ACCEPTED_FOR_PLANNING
REQUESTER_PAYS_LOW_ESTIMATE_USD=136.101850
REQUESTER_PAYS_BASE_ESTIMATE_USD=142.906680
REQUESTER_PAYS_HIGH_ESTIMATE_USD=171.488015
REQUESTER_PAYS_HIGH_SCENARIO_ACCEPTED_FOR_PLANNING=YES
SCC_STORAGE_ESTIMATE_ACCEPTED_AS_OWNER_PROVIDED=YES
FURTHER_COST_VERIFICATION_REQUIRED=NO
ACTUAL_DICOM_TRANSFER_AUTHORIZATION=NOT_YET_GRANTED
```

This closes further cost review for planning but does not authorize the transfer or convert the estimate into an invoice guarantee.

## Safety and authorization boundary

The completed supplemental attempt made exactly one bucket metadata request and zero object-list, object-GET, media, object-body, or BigQuery requests. It repeated neither the 526-page listing nor the storage audit. The aggregate safety gate and combined validator passed. No DICOM, extraction, EchoPrime, embedding, model, prediction, or confirmatory-performance operation occurred.

Full C3 remains **NO-GO**. The existing aggregate plan assumes 2,000,000,000,000 quota bytes, projects a peak of 1,611,642,076,332 bytes, and leaves 388,357,923,668 bytes. The minimum effective quota under the unchanged 200-GB headroom rule is therefore 1,811,642,076,332 bytes (approximately 1.812 TB); the preferred nominal quota remains 2 TB. The 2026-08-10 live standard SCC quota report shows only 989,000,000,000 research-tier quota bytes, so the gate fails even before exact current usage is considered. The additional one-terabyte allocation and a fresh exact-usage/filesystem receipt are required before the first body request.
