# C3 storage lifecycle and requester-pays cost plan

Status: **contract design frozen; exact-byte calculation awaits the complete metadata-only GCS preflight; full C3 remains unauthorized**.

## Scope and byte conventions

The primary project is the 4,530-study, one-study-per-subject cohort. It does not require a local mirror of the full public release. The relevant scopes are:

| Scope | Studies | Local-storage role |
|---|---:|---|
| Full public MIMIC-IV-ECHO 1.0 DICOM release | 7,243 studies and approximately 525,000 DICOM objects | Remote recoverability authority; not a primary C3 local-storage requirement |
| Historically eligible repeated-study set | approximately 7,104 studies | Separate all-study/longitudinal expansion; not C3 |
| Primary selected cohort | 4,530 studies and 4,530 subjects | Scientific cohort; every selected raw source object is retained locally during active analysis |
| Normalized selected-source requests | 335,984 candidate requests | Must be promoted by the complete metadata-only preflight before any full body transfer |

MIMIC-IV-ECHO 1.0 remains recoverable through credentialed, requester-pays GCS access. Extracted clips are deterministic derivatives and may be a rolling cache after each batch's source, extraction, embedding, pooling, checksum, preservation, and safety gates pass.

SCC Storage-as-a-Service uses whole terabytes of 1,000 GB. This plan therefore treats a 2-TB research allocation as exactly `2,000,000,000,000` bytes. The full-run headroom rule is:

`required_free_bytes = max(200,000,000,000, ceil(0.10 * quota_bytes))`.

At exactly 2 TB, the requirement is 200,000,000,000 bytes and the maximum allowed projected peak is 1,800,000,000,000 bytes.

## Current retained usage

- owner-reported `/restricted/projectnb` use before this audit: 139.17 GB;
- exact allocated bytes found under `/restricted/project/mimicecho`: 10,954,752,000 bytes;
- final quota/allocation witness: contemporaneous `pquota -u mimicecho` output;
- final current project-root usage input: contemporaneous integer-byte `du -x -B1` output for `/restricted/projectnb/mimicecho`, because the quota display may round usage.

The runtime resource calculator refuses to use the older 139.17-GB report as authority; an explicit contemporaneous integer-byte usage input is mandatory. The migration term is also runtime evidence, not a policy constant. It requires an SCC-only checksum-bound classification witness that reconciles the complete disaster-tier inventory into exact migrated and retained byte counts and states whether migration is planned or already included in the contemporaneous research-tier usage. It must not blindly add the whole `/restricted/project` tree if an approved backed-up authority subset remains there.

## Strategy A: raw plus every extracted derivative retained

This strategy retains all selected raw DICOMs and all extracted NPZ clips simultaneously.

| Component | Current authority |
|---|---|
| Current projectnb usage | Pending sealed `pquota` output |
| Migrated disaster-tier usage | `RUNTIME_CLASSIFIED_WITNESS_REQUIRED`; 10,954,752,000 bytes is the observed inventory ceiling, not the migrated-byte authority |
| Selected raw DICOMs | `PENDING_COMPLETE_GCS_METADATA_PREFLIGHT` |
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

The bucket metadata request also records the complete Autoclass fields exposed by the Cloud Storage API. The present operation-price model is authoritative only when every selected object is currently `STANDARD`, the bucket Autoclass metadata is present, and Autoclass is disabled. If any selected object is non-Standard, Autoclass is enabled, or the Autoclass status is unavailable, the cost artifact remains nonauthoritative until a storage-class/Autoclass-specific operation model is frozen. Retrieval estimates alone do not repair that operation-price gap.

Cloud Storage prices use binary GiB (`2^30` bytes). The future body-transfer estimate is therefore calculated from the exact source-byte total, not decimal TB. Requester Pays shifts request, retrieval, and network charges to the named billing project. The billing-project identifier is provided only through an SCC environment variable and must not enter Git.

The cost report separates:

1. the completed metadata-only preflight;
2. the future first-pass body GET operations;
3. network egress for exact source bytes;
4. any storage-class retrieval charge;
5. a bounded retry allowance;
6. a recommended budget contingency.

No “exact” requester-pays total is reported until bucket location, object storage classes, exact bytes, pages/operations, and destination category are all observed or explicitly frozen.

## SCC quota cost authority

Current BU documentation states **$22 per TB per year**, with a six-month minimum and fiscal-year-prorated billing. For one additional TB, the planning estimates are therefore **$11 for the six-month minimum** and **$22 for 12 months**. These are rate-derived estimates, not invoice guarantees. The exact charge remains pending the effective start date and the resulting RCS quote within the applicable fiscal year.

The SCC quota charge is an administrative purchasing gate, not a scientific gate. No monthly storage rate is used or implied by this plan.

## Copy-ready quota request

> Please reallocate the mimicecho project's 200-GB restricted backed-up baseline allocation into `/restricted/projectnb` if RCS confirms that the exchange is supported, and add one 1-TB Storage-as-a-Service increment to `/restricted/projectnb`. The intended final research-tier quota is 2,000 GB. The project will retain the selected 4,530-study raw DICOM source set and use a one-batch rolling extracted-clip cache; it will not mirror all 7,243 public studies. The exact C3 peak and headroom will be attached from the metadata-only source preflight before transfer authorization. BU's published rate is $22 per TB per year with a six-month minimum and fiscal-year-prorated billing; the estimated charge is $11 for one TB over six months or $22 over 12 months. Please provide the exact invoice amount for the requested effective start date. Before any full exchange of backed-up space, we will checksum, migrate/recreate, and recovery-test the shared Git worktrees and preserve irreplaceable restricted authorities in an approved disaster-recovery location.

If partial reallocation is supported, retaining 50 GB in `/restricted/project` is a **provisional planning option, not an established requirement or sufficiency claim**. The exact retained allocation must be at least the checksum-verified classified size of all authorities that require disaster-recovery protection, plus an approved operating margin. Only the resulting classified migration witness may supply the migrated-byte term, and the resource plan must be recalculated against the actual resulting research-tier byte quota.

## Authorization rule

Full C3 can be recommended under the intended 2-TB quota only after the generated resource report proves:

- `projected_peak_bytes <= 1,800,000,000,000`;
- one-batch cache concurrency is sufficient;
- no raw-DICOM deletion is needed;
- the backed-up authority plan is complete;
- requester-pays budget and the SCC quota quote are approved.

If the one-batch rolling plan misses the threshold, first reduce derivative concurrency and temporary/retry retention. If a policy-compliant one-batch plan still misses it, request another 1-TB increment; do not weaken raw retention, preservation, or safety gates.
