# Historical Pipeline Recovery Sprint

This is the repository-archeology and SCC-filesystem authority for the minimal
five-study canary adapter. It records no live selection and grants no cloud,
scheduler, DICOM, GPU, modeling, prediction, or confirmatory access.

## Evidence boundary

The successful full-scale implementation is the version of
`scripts/scc_run_fullscale_pipeline.sh` at
`c6e85b03a0ffde2f9175956c499079c8cd4eeecd`, together with the unchanged
submitter at `19954cb753b7f09645e2c19bd70bef0eeb1c571d`. The postrun audit and
manuscript-safe snapshot at
`23c74ccfd145ab9a423b6942a431a1894a34ab67` verify that this path produced a
coherent full-scale result: 4,530 eligible one-study-per-subject rows, 191,993
clip embeddings across 4,696 studies, 4,696 study embeddings, no missing
required audit file, and no structural warning.

`git log --follow`, `git blame`, the two historical branches
`codex/phase1-tapse-lvot-audits` and
`codex/phase2-stable-imaging-baselines`, and the snapshot were inspected. The
branches inherit the full-scale snapshot and add downstream audit/baseline
work; neither contains a different full-scale source-to-embedding runner.
Despite their names in the recovery request,
`scripts/download_mimic_echo_dicoms.py` and
`scripts/merge_clip_embeddings.py` never existed in tracked history. The
successful run used inline requester-pays `gsutil` transport and
`scripts/merge_batch_embeddings.py`, respectively.

Commit aliases used in the step table are exact:

- `RUNNER=c6e85b03a0ffde2f9175956c499079c8cd4eeecd`
- `SUBMITTER=19954cb753b7f09645e2c19bd70bef0eeb1c571d`
- `INITIAL_STAGES=8231ba6b37ae4556ba4438cb27f27162477914da`
- `AUDIT_INVOCATION_FIX=a5443c5fc4f48ddedb21028fc66bd4f4b04cde7d`
- `ENCODER_AND_POOLING=43f558acd95a1aae70fb4c9fd2b85657b20503a2`
- `BATCH_MERGE=f6187888027a3321ec76201f8403f81ee0bc22e6`
- `STAGE_D_RUNNER=91daead7c8823a5f0e76aaf625172186d24710c1`
- `SNAPSHOT=23c74ccfd145ab9a423b6942a431a1894a34ab67`

An abbreviated hash in the table always denotes the corresponding exact alias
above; paths and line references are taken from that commit's blob, not from a
later file with the same name.

## Reconstructed successful workflow

| # | Exact historical action | Commit and path evidence |
|---:|---|---|
| 1 | Query per-study DICOM counts and study linkage, require a non-null measurement link and 5–99,999 DICOMs, rank studies within subject by `FARM_FINGERPRINT(study_id, seed=20260323)`, retain exactly one per subject, and order deterministically by the same hash. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 20–32 and 43–118; result counts in `23c74ccf…:docs/results_snapshot/2026-04-01_fullscale/audit/fullscale_audit_summary.json` |
| 2 | Reuse the prior Stage-D 500-study `selected_studies.csv` and `echoprime_embeddings_512` directory: subtract its study IDs before new batching, then include its completed embedding store in the final merge. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 34–36, 122–167, and 292–303; Stage-D producer at `91daead7…:scripts/scc_run_stage_d_500study.sh` |
| 3 | Split remaining studies, in deterministic input order, into consecutive batches of at most 500 and write `batch_NNN_studies.csv` plus `batch_manifest.json`. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 122–171 |
| 4 | Submit one SCC job, not a scheduler DAG or array. The exact command was the `qsub` invocation reproduced below. | `19954cb7…:scripts/scc_submit_fullscale_job.sh` lines 15–70 |
| 5 | In the login-style job shell, initialize modules, load Python 3.10.12 and Google Cloud SDK 455.0.0, source `scc_env.sh`, verify CUDA, and launch the runner with `.venv-echoprime/bin/python` as its Python authority. | `19954cb7…:scripts/scc_submit_fullscale_job.sh` lines 20–44; runner paths at `c6e85b03…` lines 20–32 |
| 6 | Use the frozen EchoPrime weights directory; the encoder loader requires `echo_prime_encoder.pt`. Embedding extraction wrote periodic checkpoints every 5,000 clips. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 24–25 and 264–274; `43f558ac…:scripts/extract_echoprime_embeddings.py` |
| 7 | For each batch, query the exact `echo_record_list` rows, then perform requester-pays transport with `gsutil -u <private-billing-authority> -m cp -r` from the PhysioNet MIMIC-IV-ECHO bucket. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 194–243 |
| 8 | For each study, skip transport when local `.dcm` count already met the declared count; otherwise make at most three transport attempts and delete `.gstmp` remnants between attempts. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 220–243 |
| 9 | Read DICOM headers, recording dimensions, photometric interpretation, frame count/timing and read failures; write an audit, summary, and multi-frame cine-candidate manifest. | `8231ba6b…:scripts/audit_mimic_echo_dicoms.py`; invocation corrected at `a5443c5f…` and present at `c6e85b03…` lines 245–250 |
| 10 | Decode `pixel_array`, normalize supported gray/RGB forms, mask non-ultrasound pixels, resize to 224×224, and sample/pad exactly 32 frames into one NPZ per successful cine. | `8231ba6b…:scripts/extract_mimic_echo_cines.py`; full-clip invocation at `c6e85b03…` lines 252–262 |
| 11 | Load only `echo_prime_encoder.pt`, normalize frames, stride stored 32 frames to 16 encoder frames, run encoder-only EchoPrime, and write float32 512-dimensional clip vectors. | `43f558ac…:scripts/extract_echoprime_embeddings.py`; invocation at `c6e85b03…` lines 264–274 |
| 12 | Filter extraction rows to `write_ok`, require valid 224×224×32 clip tensors, record per-clip failures, checkpoint, then write the final clip NPZ, manifest, and summary. At merge, require each batch NPZ row count to equal its filtered manifest row count. | `43f558ac…:scripts/extract_echoprime_embeddings.py`; `f6187888…:scripts/merge_batch_embeddings.py` |
| 13 | Concatenate the prior Stage-D clip store and every completed batch store as float32, retaining the merged clip manifest and summary. | `f6187888…:scripts/merge_batch_embeddings.py`; invocation at `c6e85b03…` lines 287–311 |
| 14 | Group clip rows by study and mean-pool them to float32 study-level embeddings, with clip-count statistics in the summary. | `43f558ac…:scripts/aggregate_study_embeddings.py`; invocation at `c6e85b03…` lines 313–325 |
| 15 | Resume at three levels: reuse `all_eligible_studies.csv` only when it existed with more than 100 lines; skip a study download when local count met declared count; skip an entire batch when its final `clip_embeddings_512.npz` existed. A periodic embedding checkpoint was evidence, not an automatic checkpoint-restart protocol. Terminal success was the runner's `=== Pipeline complete ===` log, later corroborated by the strict postrun audit. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 47–49, 185–189, 227–243, and 441–442; audit at `c6e85b03…:scripts/audit_fullscale_outputs.py`; frozen result at `23c74ccf…` |
| 16 | Purge only after the embedding subprocess returned success under `set -e`: unconditionally remove the shared batch download `files/` tree and that batch's extracted all-clip NPZ root, then report free space. There was no pre-purge checksum, exact-study completeness receipt, or later merge validation at this point. | `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh` lines 1–2 and 276–285 |
| 17 | Retain study/batch manifests, record queries/CSVs, header audits, extraction manifests, per-batch final clip embedding NPZ/manifests/summaries, merged clip store, study store, structured/split/label artifacts, audit/reporting outputs, and the frozen/snapshot aggregate evidence. Raw DICOM bodies and per-clip extracted NPZs were not retained. | Full output layout in `c6e85b03…:scripts/scc_run_fullscale_pipeline.sh`; audited required files and counts plus snapshot manifest at `23c74ccf…:docs/results_snapshot/2026-04-01_fullscale/` |

The post-aggregation measurement export, LVEF manifest construction, and
E2b/E3/E5 model fitting in historical steps 6–8 are evidence that the runner
continued successfully; they are expressly outside the minimal canary.

### Exact qsub and resources

The effective command in `19954cb7…:scripts/scc_submit_fullscale_job.sh` was:

```text
qsub -o <job-dir> \
  -l h_rt=48:00:00 \
  -l gpus=1 \
  -l gpu_c=8.0 \
  -l gpu_memory=48G \
  -pe omp 4 \
  -l mem_per_core=4G \
  <generated-job-script>
```

The generated job script supplied `-P mimicecho`, `-N echo_fullscale`, `-j y`,
and `-m ea`. No queue was named. The submitter called bare `qsub`, resolved by
the initialized SCC environment.

### Purge consequence

The purge did what its 800-GB working-space design intended, but successful
completion does not make it scientifically ideal. It happened before the
cross-batch merge and full-scale postrun audit, and it destroyed the raw DICOM
bodies and extracted clip NPZs needed to re-establish physical-source identity
or independently replay pixel decoding. Today the retained manifests and
embeddings establish logical lineage and aggregate success, not historical
physical-source authority. The canary therefore performs no purge. A future
full run may preserve the historical batch-level resume pattern, but any purge
must occur only after an explicit validated preservation receipt.

## SCC filesystem and scheduler semantics

Read-only, aggregate-safe SCC metadata inspection found:

| Object class | Filesystem/topology | Owner/group relationship | Observed mode | Symlink/bind/ownership conflict |
|---|---|---|---:|---|
| Historical successful full-scale output root | `/restricted/project` NFS | project owner and project group | `2755` | none |
| Historical `batches/` root and 18 immediate batch directories | same NFS | same owner/group | `2755` (all 18 children) | none |
| Current restricted-project parent | `/restricted/projectnb` NFS | root-owned, expected project group | `2770` | none |
| Current C3 root | same NFS | current owner, expected project group | `2700` | none |
| Current `owner_private/` and private credential/config directory | same NFS | current owner, expected project group | `2700`; credential file `0600` | none |
| Failed materialization child `exact_five_canary/` | same parent | rollback removed it | absent | no surviving child to inspect |

Thus historical scientific outputs had group/other read/search bits (`055`),
whereas current private directories have zero group/other permission bits.
`0700` did not appear as the effective private-directory mode in the observed
setgid topology; `2700` did. There was no observed `0770`; `2770` was the
restricted-project parent. The different historical/current NFS roots are a
topology difference, not a bind mount or ownership conflict.

The current materializer calls `os.mkdir(path, 0o700)` below the SCC setgid
parent, then compares `stat.S_IMODE(st_mode)` literally with `0o700`
(`scripts/lvef_c3_canary_authority_materializer.py`, `_require_private_directory`).
On this SCC topology the inherited setgid bit makes the resulting secure mode
`2700`: owner `0700` is present, group/other permission bits remain zero, and
setgid is the only special bit. The program therefore rejects a directory it
created itself. The effective private-directory predicate is:

- regular directory, no leaf or ancestor symlink, checked without following;
- expected owner and group;
- `(mode & 0700) == 0700`;
- `(mode & 0077) == 0`;
- no special bit except optional setgid.

Credential files remain exactly owner-private regular files at `0600`; their
protection is not weakened.

SCC scheduler metadata likewise distinguishes execution from representation.
`/usr/local/bin/qsub` and `/usr/local/bin/qstat` are root-owned mode-`0777`
symlinks to root-owned mode-`0755` canonical regular executables. Historical
success used bare `qsub`. The failed canary predicate treated the lexical alias
as if it had to be a no-follow regular executable with a sealed file identity;
that is an overstrict scheduler-path representation assertion, not an SCC
scheduler failure and not inability to resolve the intended binary. Current
tracked constants point at the canonical regular executables. No scheduler
command was run during this audit.

## Every pre-body gate and its disposition

The 65 currently blocking checks classify as 11
`ESSENTIAL_SCIENTIFIC`, 18 `ESSENTIAL_DATA_GOVERNANCE`, 7
`ESSENTIAL_COST_OR_ACCESS`, 3 `USEFUL_NONBLOCKING`, 4 `REDUNDANT`, 8
`SCC_INCOMPATIBLE`, 3 `DEAD_OR_UNREACHABLE`, and 11
`DUPLICATED_BY_ANOTHER_AUTHORITY`. The 36 essential checks collapse into 15
composite controls in the minimal route; 12 redundant or SCC-incompatible
gates are removed or bypassed. The 11 duplicated and 3 dead families are also
retired from the controlling route, while the 3 useful diagnostics remain
nonblocking.

| ID | Current blocking gate | Classification | Historical/minimal disposition |
|---:|---|---|---|
| 01 | Protected shell, `umask 077`, sanitized Git/Python/cloud environment | ESSENTIAL_DATA_GOVERNANCE | Keep in submitter/job launcher. |
| 02 | Fixed SCC worktree, tracked controller, one recognized argument | ESSENTIAL_DATA_GOVERNANCE | Keep one fixed worktree and short command. |
| 03 | Exact EchoPrime Python, no-symlink ancestry, safe mode, pinned hash | ESSENTIAL_SCIENTIFIC | Keep once at submit and recheck job-side through environment binding. |
| 04 | Four controller modes (`validate/prepare/preflight/execute`) | REDUNDANT | Replace controlling live surface; deprecate old modes. |
| 05 | Six-argument per-stage launcher, five stage IDs, `JOB_ID`, packet path | SCC_INCOMPATIBLE | Replace with one sequential job launcher. |
| 06 | Queued job reauthenticates launcher/Python/state/worker/HEAD/tree | ESSENTIAL_DATA_GOVERNANCE | Keep once at start for minimal runner and exact commit. |
| 07 | Governing HEAD, clean tree, no untracked imports, safe tracked controls | ESSENTIAL_DATA_GOVERNANCE | Keep over the small controlling file set. |
| 08 | Exact branch, local=origin, three historical ancestor checks | USEFUL_NONBLOCKING | Exact authorized HEAD blocks; remainder is diagnostic. |
| 09 | Four packet/preselection/state/manifest closed schemas | DUPLICATED_BY_ANOTHER_AUTHORITY | Retain only manifest and simple state schemas. |
| 10 | Five-stage scheduler-plan/callable installation validation | SCC_INCOMPATIBLE | Validate one runner and one resource profile. |
| 11 | Attempt-004/preparation-005/capacity path chain | DUPLICATED_BY_ANOTHER_AUTHORITY | Preserve as history, not a canary gate. |
| 12 | Production-attempt-005 packet identity/binding | DUPLICATED_BY_ANOTHER_AUTHORITY | Bind needed artifacts directly in the manifest. |
| 13 | Current-environment receipt commit/bytes/hash/chain | ESSENTIAL_SCIENTIFIC | Keep direct manifest binding and job-side recheck. |
| 14 | Frozen EchoPrime checkpoint path/size/hash | ESSENTIAL_SCIENTIFIC | Keep direct binding and job-side recheck. |
| 15 | Study/source/metadata/split stat/size/hash bindings | ESSENTIAL_SCIENTIFIC | Keep source-integrity bindings in manifest. |
| 16 | gcloud resolution/ADC and CRC runtime identities | ESSENTIAL_COST_OR_ACCESS | Keep immediately before download. |
| 17 | Requester-pays syntax, private env-only, never argv | ESSENTIAL_COST_OR_ACCESS | Keep; manifest names the variable, not its value. |
| 18 | Installation/private/conflict/quota checks repeated across five layers | REDUNDANT | Run each invariant once, then time-of-use checks job-side. |
| 19 | Local private fallback and inconsistent unreachable execute branches | DEAD_OR_UNREACHABLE | Exclude from controlling route. |
| 20 | Materializer HEAD and prepare/seal permission recheck | ESSENTIAL_DATA_GOVERNANCE | One authorized commit/scope check. |
| 21 | Private directories/files, no symlinks, O_EXCL/no-clobber | ESSENTIAL_DATA_GOVERNANCE | Keep effective SCC-compatible permissions. |
| 22 | Transaction marker/adoption/manual rollback authority | REDUNDANT | Fresh run root plus atomic promotion, no transaction authority. |
| 23 | Identifier-free preselection hard/scheduler authority | DUPLICATED_BY_ANOTHER_AUTHORITY | Owner authorization, commit, and sealed manifest suffice. |
| 24 | Stable qsub executable identity | ESSENTIAL_DATA_GOVERNANCE | Check canonical executable once before sole qsub. |
| 25 | qstat inode, `ps` token scan, user/job-name scan | USEFUL_NONBLOCKING | Diagnostic; fresh run root/state prevents duplicates. |
| 26 | Native quota/current filesystem headroom | ESSENTIAL_COST_OR_ACCESS | Keep once before selection/seal/submission. |
| 27 | Row-bearing reads ordered after capacity/access gates; private projections | ESSENTIAL_DATA_GOVERNANCE | Keep ordering and restricted handling. |
| 28 | Historical cine and prior-smoke exclusion hashes/results | ESSENTIAL_SCIENTIFIC | Keep source bindings and exclusion result. |
| 29 | Release/metadata/split reconciliation and source completeness | ESSENTIAL_SCIENTIFIC | Keep before selection. |
| 30 | Deterministic five studies/five subjects/train; no-cine/smoke excluded | ESSENTIAL_SCIENTIFIC | Keep unchanged. |
| 31 | Prohibited outcome/prediction/performance fields and scopes | ESSENTIAL_DATA_GOVERNANCE | Keep in manifest and runner. |
| 32 | At most 750 objects and 5 GB | ESSENTIAL_COST_OR_ACCESS | Keep at seal and immediately pre-body. |
| 33 | Closed canonical exact-five manifest and private no-clobber seal | ESSENTIAL_DATA_GOVERNANCE | Make it the single controlling authority. |
| 34 | Separate candidate/source projection authorities | DUPLICATED_BY_ANOTHER_AUTHORITY | Optional evidence only; never a gate. |
| 35 | Serialized immutable batch-plan authority | DUPLICATED_BY_ANOTHER_AUTHORITY | Derive one batch in memory; any file is a receipt. |
| 36 | Scientific call order and predecessor/output receipts | ESSENTIAL_SCIENTIFIC | Direct sequential flow; validate returned artifact before next call. |
| 37 | Five-submission/one-GPU scheduler topology | SCC_INCOMPATIBLE | One job resource tuple and runner. |
| 38 | Exact-membership resume/download ledger | ESSENTIAL_DATA_GOVERNANCE | Keep as run-internal object journal, not authority. |
| 39 | Separate body-only grant with expiry/request ceiling | DUPLICATED_BY_ANOTHER_AUTHORITY | Remove it; manifest ceilings, the tracked retry contract, and qsub walltime bound the run. |
| 40 | Four additional per-stage grant files | DUPLICATED_BY_ANOTHER_AUTHORITY | Remove; sequential predecessors suffice. |
| 41 | Giant packet restating manifest/plans/grants/tools/runtime/scope | DUPLICATED_BY_ANOTHER_AUTHORITY | Job reads manifest, simple state, tracked code/config. |
| 42 | Five-state lifecycle | SCC_INCOMPATIBLE | Use `PREPARED` → `RUNNING` → `PASS`/`FAIL`. |
| 43 | Manifest/run/commit identity and fresh output before execution | ESSENTIAL_DATA_GOVERNANCE | Keep as one fresh-run check. |
| 44 | Helper permits executing state that caller rejects | DEAD_OR_UNREACHABLE | No reentry; accept exactly `PREPARED`. |
| 45 | Private no-clobber run/log/work/result layout | ESSENTIAL_DATA_GOVERNANCE | Keep one simpler run root. |
| 46 | Durable submit claim and validated scheduler job ID | ESSENTIAL_DATA_GOVERNANCE | Keep one claim and job-ID receipt. |
| 47 | Five qsubs, holds, snapshots, DAG status | SCC_INCOMPATIBLE | One qsub; no hold or scheduler-stage ledger. |
| 48 | Five CPU/GPU resource profiles | SCC_INCOMPATIBLE | One SCC-valid GPU profile. |
| 49 | No arrays/resubmission/production continuation | ESSENTIAL_DATA_GOVERNANCE | Keep as runner invariants. |
| 50 | Worker reprojects giant packet/plan/grant/tool hashes | DUPLICATED_BY_ANOTHER_AUTHORITY | Validate manifest/state/commit/env/checkpoint once. |
| 51 | All five jobs submitted plus per-stage `JOB_ID` gate | SCC_INCOMPATIBLE | Check sole job/submit receipt once. |
| 52 | Per-stage execution claims and scheduler predecessor chain | SCC_INCOMPATIBLE | Direct calls/exceptions sequence; retain artifact checks. |
| 53 | Scientific predecessor receipts, ledger, membership before extract/embed | ESSENTIAL_SCIENTIFIC | Keep between direct calls. |
| 54 | Terminal state tied to preservation/finalization and safe receipt | ESSENTIAL_DATA_GOVERNANCE | Keep; smallest safe failure or validated success. |
| 55 | Batch plan membership and journal/runtime validation pre-download | ESSENTIAL_SCIENTIFIC | Keep; derive plan in memory and bind journal to manifest. |
| 56 | Separate body-grant owner/batch/plan/expiry/request validation | DUPLICATED_BY_ANOTHER_AUTHORITY | Validate manifest/plan membership, object/byte ceilings, and the tracked per-object request budget directly; no separate expiry grant. |
| 57 | Recheck five/one-batch/750/5GB and scoped root | ESSENTIAL_COST_OR_ACCESS | Keep immediately before first token/body. |
| 58 | Exact output root, private nonsymlink dirs, declared scope | ESSENTIAL_DATA_GOVERNANCE | Keep under one run root. |
| 59 | No-clobber download transitions and pre-access object journal | ESSENTIAL_DATA_GOVERNANCE | Keep unchanged as run evidence. |
| 60 | Verified/resume/unbound-transaction recovery branches | DEAD_OR_UNREACHABLE | Fresh no-resubmit canary cannot reenter. |
| 61 | Exact object key/generation/size/MD5/CRC/uniqueness | ESSENTIAL_SCIENTIFIC | Keep at seal and download verification. |
| 62 | Bounded object attempts/backoff/request budget, increment before token | ESSENTIAL_COST_OR_ACCESS | Keep; no whole-job retry. |
| 63 | ADC/token authority before download; body begins at transport fetch | ESSENTIAL_COST_OR_ACCESS | Keep exact first-body boundary. |
| 64 | Full synthetic live-path preflight | USEFUL_NONBLOCKING | Keep as acceptance, not a live authority. |
| 65 | Second standalone synthetic manifest/plan check | REDUNDANT | Merge into the one end-to-end fixture. |

The current gate sources are
`scripts/scc_run_lvef_c3_canary.sh`, `scripts/lvef_c3_canary.py`,
`scripts/lvef_c3_canary_authority_materializer.py`,
`scripts/lvef_c3_canary_execution_authority.py`,
`scripts/lvef_c3_canary_scheduler_plan.py`,
`scripts/lvef_c3_canary_dispatch.py`,
`scripts/lvef_c3_canary_stage_worker.py`,
`scripts/lvef_c3_orchestration_core.py`,
`scripts/lvef_c3_canary_manifest.py`, and the corresponding tracked configs.
The row numbers and classifications above were derived from the controlling
code at `59a033166525c7488242cbecc6a4aff6582cc7bc`.

## Minimum viable execution design

The 15 controlling composites are: protected launcher; exact commit and
runtime; one sealed source-integrity manifest; deterministic exclusions and
train-only exact-five membership; object/byte ceilings; requester-pays and
access runtime; checkpoint validation; quota/headroom; effective private
permissions and fresh run root; one scheduler executable/resource/submit
claim; four-state lifecycle; sequential scientific predecessor validation;
durable object integrity/retry journal; prohibited-scope and aggregate-export
guard; and preservation/final receipt.

The controlling route is one already-sealed restricted manifest, one unique
run root, one short tracked submitter
(`scripts/scc_submit_lvef_c3_minimal_canary.sh`), and one SCC GPU job launched
through `scripts/scc_run_lvef_c3_minimal_canary.sh`. The job changes
`PREPARED` to `RUNNING`, then directly calls the existing production download,
DICOM audit/extraction, EchoPrime embedding, clip validation/merge, study mean
pooling, preservation, and final-summary functions. Every stage validates its
returned artifacts before the next call. It writes one durable, atomically
promoted stage ledger and one terminal aggregate-safe receipt, promotes unique
partial files atomically, and never purges, retires cache, fits a model,
predicts an endpoint, or accesses confirmatory performance.

The manifest is the scientific/access authority; the four-state file is only
the run lifecycle. Derived plans and journals are receipts inside the run root,
not additional authorities. Production stage code is reused, not copied. The
existing multi-packet/five-qsub canary remains tracked for historical
comparison but is deprecated and noncontrolling.
