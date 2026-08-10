# C3 storage lifecycle and requester-pays cost plan

Status: **exact source, rolling-cache resource, and rate-explicit cost plans adjudicated; full C3 remains unauthorized**.

## Scope and byte conventions

The primary project is the 4,530-study, one-study-per-subject cohort. It does not require a local mirror of the full public release. The relevant scopes are:

| Scope | Studies | Local-storage role |
|---|---:|---|
| Full public MIMIC-IV-ECHO 1.0 DICOM release | 7,243 studies and approximately 525,000 DICOM objects | Remote recoverability authority; not a primary C3 local-storage requirement |
| Historically eligible repeated-study set | approximately 7,104 studies | Separate all-study/longitudinal expansion; not C3 |
| Primary selected cohort | 4,530 studies and 4,530 subjects | Scientific cohort; every selected raw source object is retained locally during active analysis |
| Normalized selected-source requests | 335,984 verified requests totaling 1,216,569,133,322 bytes | Current public-source inventory authority; not historical byte-identity authority |

MIMIC-IV-ECHO 1.0 remains recoverable through credentialed, requester-pays GCS access. Extracted clips are deterministic derivatives and may be a rolling cache after each batch's source, extraction, embedding, pooling, checksum, preservation, and safety gates pass.

SCC Storage-as-a-Service uses whole terabytes of 1,000 GB. This plan therefore treats a 2-TB research allocation as exactly `2,000,000,000,000` bytes. The full-run headroom rule is:

`required_free_bytes = max(200,000,000,000, ceil(0.10 * quota_bytes))`.

At exactly 2 TB, the requirement is 200,000,000,000 bytes and the maximum allowed projected peak is 1,800,000,000,000 bytes.

## Current retained usage and live capacity

Immutable parent research attempt `lvef_multitask_phase1ee_post_expansion_capacity_attempt_001` produced `lvef_c3_live_quota.summary.json` (2,257 bytes; SHA-256 `267bf03d8f059b4a71ebe0754015af4a710edea37c060e3e392642e1ad335d71`). Composite successor `lvef_multitask_phase1ee_post_expansion_capacity_attempt_002` is bound to implementation commit `0800a0b4de93911cc39467acf2460a8d5ed6135a`; its 5,003-byte aggregate has SHA-256 `28fad54a68f84165cb8340c3e666de84e1f6efc6bf20b146bc7bc006d9d4171c`. It hash-revalidated the immutable parent and captured only contemporaneous control-tier evidence; no project-quota or research-filesystem command, storage inventory, source reconciliation, or cloud operation was repeated.

| Capacity authority | Exact value |
|---|---:|
| Research quota / usage / quota-available | 1,989,000,000,000 / 150,387,011,072 / 1,838,612,988,928 bytes |
| Research physical filesystem available | 2,092,672,483,328 bytes |
| Research file quota / used / available | 33,554,432 / 106,228 / 33,448,204 |
| Backed control quota / usage / quota-available | 11,000,000,000 / 10,959,364,608 / 40,635,392 bytes |
| Backed control physical filesystem available | 2,046,820,352 bytes |
| Backed control file quota / used / available | 360,448 / 47,189 / 313,259 |

Research byte quota, file quota, minimum effective quota, underlying physical availability, and the frozen 200-GB reserve all pass. The research quota leaves 377,357,923,668 bytes after the 1,611,642,076,332-byte frozen peak; the physical filesystem leaves 431,417,418,068 bytes after required projected writes and reserve. The backed control tier passes file count but fails its byte-margin gate: 40,635,392 quota bytes are not sufficient for the prespecified 10,000,000,000-byte maximum additional control-plane write burden.

The migration term remains runtime evidence rather than a policy constant. Its checksum-bound planning witness records 2,841,265,664 planned bytes but remains `PLANNED_NOT_EXECUTED`; it neither proves migration nor permits adding those bytes to current usage. A future completion witness must state whether migrated bytes are already included in contemporaneous research-tier usage and must not blindly add the whole `/restricted/project` tree if an approved backed-up authority subset remains there.

## Strategy A: raw plus every extracted derivative retained

This strategy retains all selected raw DICOMs and all extracted NPZ clips simultaneously.

| Component | Current authority |
|---|---|
| Current projectnb quota/usage | Sealed evidence: 1,989,000,000,000-byte quota and 150,387,011,072 usage bytes; the separate management-page display remains rounded and is not used as exact |
| Planned disaster-tier migration | Planning classification passed for 2,841,265,664 bytes and remains `PLANNED_NOT_EXECUTED`; 10,954,752,000 bytes is the separate historical inventory total, not an executed-migration amount |
| Selected raw DICOMs | 1,216,569,133,322 bytes across 335,984 current metadata-verified selected objects |
| All extracted clips | Historical planning estimate approximately 288 GB; not an authority for the prospective run |
| Clip embeddings | About 378,007,552 bytes for 184,574 vectors at `float32 x 512`; final prospective count may differ |
| Study embeddings | About 9,267,200 bytes for 4,525 vectors at `float32 x 512`; final imaging eligibility may differ |
| Manifests, logs, preservation, retry, and safety reserves | Must be explicit in the sealed resource JSON |

The historical 288-GB derivative estimate cannot establish a safe peak because cohort-wide prospective cine candidacy is not yet known. A fail-closed pre-extraction upper bound is the verified object count multiplied by `32 * 224 * 224 * 3 = 4,816,896` uncompressed bytes, plus container/manifest overhead. Under that bound, retaining every extraction could exceed the 2-TB contract. Strategy A is therefore **not the recommended 2-TB operating mode** before cohort-wide DICOM audit evidence exists.

## Strategy B: preferred rolling derivative cache

This strategy retains every selected raw DICOM and all long-lived authorities, but permits only one production extraction batch at a time initially.

1. Download each exact generation into an isolated partial path; atomically promote only after size and remote MD5 verification.
2. Retain the verified raw DICOM permanently during active analysis.
3. Audit headers and extract one deterministic component batch.
4. Stream clip minibatches through the pinned EchoPrime encoder; do not load the cohort or component into RAM.
5. Produce batch clip/study manifests, embeddings, source hashes, environment receipt, gate result, and preservation manifest.
6. Independently verify the complete batch authority.
7. Prepare a checksum-bound retirement plan for that batch's extracted NPZ cache.
8. Retire only exact manifest-listed NPZ files after separate retirement authorization; never expose a raw-DICOM deletion operation.
9. Retain a deterministic audit sample of extracted clips only if its scientific purpose and byte budget are frozen in advance.

The active extraction-cache upper bound is computed from the largest preflight production-batch object count, not the four-study smoke's compression ratio. One-batch concurrency is the default. Two-batch concurrency is permitted only if the sealed two-batch peak independently leaves the required 200-GB headroom.

The final resource calculator must include, without double counting:

- current projectnb allocated bytes;
- exact migrated bytes;
- exact selected raw source bytes;
- active extraction-cache bound;
- clip and study embeddings;
- restricted and aggregate manifests;
- scheduler/application logs;
- one shared full-batch transfer/retry buffer covering incomplete-object partials and bounded retries without double counting;
- preservation outputs;
- a separate operating safety reserve;
- the 200-GB free-headroom gate.

Partial download behavior is object-atomic. The resource plan carries one shared reserve equal to the largest complete source batch. That same reserve covers incomplete-object partials and retry overlap; separate full-batch `partial` and `retry` terms are prohibited because they would double count the same failure envelope. The contract may retain only a bounded set of failed object partials and must not budget or create a second copy of an entire batch. Node `$TMPDIR` can hold ephemeral decoder work, but never the only completed object or authority.

## GCS pricing model

The price authority is the current [Google Cloud Storage pricing table](https://cloud.google.com/storage/pricing) plus [Requester Pays documentation](https://docs.cloud.google.com/storage/docs/requester-pays). The sealed cost JSON records the retrieval date and assumptions.

For a standard-storage US multi-region source downloaded over the public Internet to SCC in Massachusetts, the current model is:

- full-body network transfer: `$0.12/GiB` for the applicable first tier;
- Class B object reads/metadata gets: `$0.0004 per 1,000` operations;
- Class A US multi-region listing: `$0.01 per 1,000` operations;
- standard-storage retrieval fee: `$0/GiB`;
- Nearline/Coldline/Archive retrieval, if observed, must be added at the current per-GiB rate;
- metadata-only listing transfers no object bodies; listing response bytes are separately bounded and recorded.

The immutable listing established that every selected object is currently `STANDARD`. A subsequent one-request receipt proved that the `autoclass` key was absent from a successful explicitly projected JSON API v1 `buckets.get`. Primary JSON API v1 and Storage v2 documentation establishes that absent configuration is disabled, so the effective state is `ABSENT_CONFIGURATION_DEFAULT_DISABLED`. JSON null, malformed values, and empty/incomplete mappings remain unresolved rather than being treated as absence.

Cloud Storage prices use binary GiB (`2^30` bytes). The future body-transfer estimate is therefore calculated from the exact source-byte total, not decimal TB. Requester Pays shifts request, retrieval, and network charges to the named billing project. The billing-project identifier is provided only through an SCC environment variable and must not enter Git.

The cost report separates:

1. the completed metadata-only preflight;
2. the future first-pass body GET operations;
3. network egress for exact source bytes;
4. any storage-class retrieval charge;
5. a bounded retry allowance;
6. a recommended budget contingency.

The rate-explicit planning estimate is now authoritative within its stated assumptions: low $136.101850, base $142.906680, and high $171.488015. The original-formula values were $136.101859, $142.906689, and $171.488027. The only numeric correction reclassifies the original bucket metadata GET from Class A to Class B and includes the supplemental GET as a second Class B operation. This remains a planning estimate, not an invoice guarantee.

### Frozen owner disposition

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

These values are frozen for the current planning stage. No additional cost investigation, recalculation, or independent verification is required. This disposition closes only the planning-cost gate; it neither guarantees an invoice nor authorizes a transfer.

## Completed 2-TB rolling-cache projection

The immutable aggregate resource plan uses a 2,000,000,000,000-byte quota, a frozen planning input of 152,275,355,648 current-usage bytes, a one-batch 92,286,910,464-byte active extraction cache, 1,376,190,464 bytes of clip embeddings, 18,554,880 bytes of study embeddings, and a 50,000,000,000-byte safety reserve. It projects a peak of 1,611,642,076,332 bytes and 388,357,923,668 bytes of headroom. Thus, the planned 2-TB allocation passes the 200-GB headroom rule. The minimum effective quota under this exact frozen plan is 1,811,642,076,332 bytes; the preferred nominal quota remains 2 TB. Phase 1E-D did not silently recompute this immutable plan using the later 150,386,971,136-byte live-usage observation.

The post-expansion receipt proves the research allocation is active at 1,989,000,000,000 bytes. It leaves 377,357,923,668 quota bytes after the frozen peak, exceeding the unchanged 200,000,000,000-byte reserve by 177,357,923,668 bytes. Independently, 2,092,672,483,328 physical filesystem bytes are available, leaving 431,417,418,068 bytes after required projected writes and reserve. Another purchased terabyte is not indicated by the current one-batch plan. This research-capacity ruling does not cure the backed control-tier byte failure or authorize transfer.

## SCC quota cost authority

The owner-provided SCC storage estimate is accepted for planning. The previously recorded planning basis is **$22 per TB per year**, with a six-month minimum and fiscal-year-prorated billing, corresponding to **$11 for the six-month minimum** or **$22 for 12 months** for one additional TB. No further cost verification is required for this project-stage gate.

The SCC quota charge is an administrative purchasing gate, not a scientific gate. No monthly storage rate is used or implied by this plan.

## Copy-ready free-pool reallocation request

> The machine receipt proves a current research quota of 1,989,000,000,000 bytes, which passes the frozen C3 minimum, physical-filesystem, file-count, and 200-GB-reserve gates. The owner-attested administrative composition assigns the purchased 1,000-GB Storage-as-a-Service allocation wholly to `/restricted/projectnb`; please leave that purchased allocation unchanged. The current backed tier is only 11,000,000,000 bytes, with 10,959,364,608 bytes used and 40,635,392 bytes available, so it fails the control-plane operating-margin gate. Please reallocate the free baseline pool to the preferred 50-GB backed / 950-GB non-backed split, producing total quotas of 50,000,000,000 bytes backed and 1,950,000,000,000 bytes research. If 50 GB cannot be retained, the minimum acceptable option is 25 GB backed and 1,975 GB research. After activation, the project will capture one fresh read-only quota/filesystem/file-count receipt before any DICOM body request.

Both options preserve the frozen research gate. At the 1,611,642,076,332-byte peak, the preferred 1,950-GB research allocation leaves 338,357,923,668 bytes; the minimum 1,975-GB allocation leaves 363,357,923,668 bytes. The preferred 50-GB control tier gives the safer margin for Git/common-repository writes, owner-private receipts, scheduler logs, environment metadata, and preservation control artifacts. No administrative change is authorized by this document.

## Authorization rule

Full C3 can be recommended only after the generated live resource report proves:

- `effective_quota_bytes - projected_peak_bytes >= 200,000,000,000`;
- sufficient physical-filesystem and file-count capacity independently of quota;
- a backed control allocation with its required byte and file operating margins;
- one-batch cache concurrency is sufficient;
- no raw-DICOM deletion is needed;
- the backed-up authority plan is complete;
- requester-pays and SCC planning costs retain their frozen owner-accepted disposition;
- an explicit owner authorization for the first DICOM transfer is subsequently granted.

The current research tier already passes the one-batch threshold, so another purchased terabyte is not indicated. The remaining administrative capacity action is a free-pool reallocation that restores the backed control margin while retaining at least the 1,811,642,076,332-byte research minimum. Do not weaken raw retention, preservation, or safety gates.
