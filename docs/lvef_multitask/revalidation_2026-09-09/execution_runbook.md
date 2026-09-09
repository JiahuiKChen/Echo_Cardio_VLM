# Restricted execution and evidence record

This is the analysis phase after completed C3. The original SCC producer checkout,
attempt, receipts and historical partial remain preserved. New code runs from a
separate analysis worktree; new restricted artifacts live in a separate analysis
directory. The completed preparation used commit
`073d3883fc54c4041a043efce850ecfeca07890a` in the separate SCC
`Echo_Cardio_VLM_lvef_multitask_asa_20260909` checkout; the original
`cda842d04cc18eb3669ad5377c31a6e955fc43cb` checkout was preserved.
No reconstruction job, new encoder run or cohort download is needed.

## Authorization and prerequisites

The owner's 2026-09-09 instruction is bound by SHA-256
`f8d3285a2615af4321a416f4850e078a9b5180708c43fe7ff2c87a58a5849d8d`.
`lvef_revalidation_authority.owner_authorization` records its permitted stages,
conditional on genuine clinical adjudication and the analysis lock. It does not
answer a clinical question. The current restricted clinician questionnaire has
eight unanswered decisions; the [dated readiness record](../analysis_readiness_2026_09_09.md)
governs their disposition. Owner authorization, source and environment receipts
have been published; their hashes are recorded below. They do not replace the
clinical response or final panel/dependency approval.

`prepare_lvef_revalidation_inputs.py` consumes the small completed C3 handoff and
hash-bound canonical study array, study manifest/store and clip index. It checks
one-subject/one-study ownership, the original split, exact prespecified no-cine
membership and all canonical clip/study membership. It rebuilds observed labels
and all three modalities' common ordered rows without fitting. It never reloads
the 19 batch embedding arrays or repeats the 42-artifact preservation replay.

The complete registry provides structured unit metadata. Held-out numeric
availability cannot change a training feature's unit interpretation. Only
compatible target-unit rows enter a median: dimensions and VTI are harmonized to
mm and velocities to cm/s. The exact-name LVEF label retains its separately
established EF-percentage-point analytical scale. Non-numeric/unknown/incompatible
labels are missing, never imputed. The full raw-to-canonical closure remains
restricted and governs aliases outside the candidate scored panel as well.

Clinical responses are replayed against the exact questionnaire and source rows.
The nine technical decisions bind the current evidence packet. Final panel and
dependency records explicitly bind target aggregation, construct identity,
positive predictor allowlists and prohibited families. Mixed or unresolved
clinical definitions stay excluded; a same-construct answer cannot authorize
merging fields with incompatible units. Candidate21 is an input audit, not an
automatic score panel. Non-LVEF margins may remain unresolved.

## Stage boundaries

Every runner invocation requires a private, hash-bound run manifest. Its source
files must equal their clean tracked bytes at the analysis commit. The run
manifest binds the specification, owner instruction, input audit and all seven
gate parameter sets. A caller-supplied `PASS` boolean is insufficient.

| Stage | Entry point | Evidence / behavior |
|---|---|---|
| Input preparation | `prepare_lvef_revalidation_inputs.py` | Private split arrays/rows and candidate support/fingerprint receipt; no fitting authority |
| Review preparation | `prepare_lvef_revalidation_review.py` | Bounded technical dispositions and grouped pending clinical/dependency drafts; no invented clinician answers |
| Validate | `run_lvef_revalidation.py validate` | Replays actual clinical, technical, panel, common-input, environment, safety and synthetic-test evidence |
| Seal | `run_lvef_revalidation.py validate --seal` | Exclusively publishes concrete analysis lock after all requirements pass |
| Development | `run_lvef_revalidation.py development` | Loads train/validation only; masks before transforms; fixed grids; no train-plus-validation refit |
| Freeze | `run_lvef_revalidation.py freeze` | Replays serialized coefficients/transforms/calibration and binds model freeze |
| Test release | `run_lvef_revalidation.py release-test` | Publishes the existing owner-authorized fixed-test release bound to frozen models |
| Evaluate | `run_lvef_revalidation.py evaluate` | Claims one evaluation before the sole test loader; validates every target before prediction |
| Report | `run_lvef_revalidation.py report` | Fixed-model paired inference; no tuning, row trimming or panel changes |
| Figures/tables | `render_lvef_revalidation_results.py` | Validates the aggregate bundle; draft templates contain no performance estimates |

Each runner command also takes `--run-manifest` and
`--run-manifest-sha256`. Validation and fitting cannot proceed with an unsigned
clinical response or an unlocked panel. Reusing another filename cannot bypass
the fixed `test_evaluation.claim.restricted.json` claim. An interrupted claimed
test evaluation requires evidence reconciliation; it is not permission to start
a new test-informed design.

The primary strict construct and required no-indicator sensitivity remain
separate conditions. Prespecified simulated missingness uses frozen training
patterns and already selected models; it does not create a new fitting search.
Secondary constructs or missingness simulations never activate a replacement
core Holm family. The strict core remains exactly four claims.

## Environment and resource planning

The SCC C3 Python environment was inspected without modification: Python 3.10.12,
NumPy 2.2.6, pandas 2.3.3, SciPy 1.15.3, scikit-learn 1.7.2, PyYAML 6.0.3 and
Matplotlib 3.10.8. Pytest 8.3.5 was installed into a separate private analysis
directory, not into that environment. Scientific dependency updates require an
isolated analysis environment and new environment/test bindings.

A synthetic benchmark, with four BLAS threads and no project records, used
3,171 training rows, 679 validation rows and 552 predictors. A seven-alpha fusion
Ridge grid took approximately 0.05 seconds and a seven-C fusion logistic grid
approximately 0.42 seconds on the local benchmark host. A 100-draw, three-modality
continuous-metric collection at 679 synthetic test rows took about 0.075 seconds;
the corresponding binary collection took about 0.48 seconds. These are workload
probes, not SCC production measurements or clinical performance results.

Allow **45–90 minutes for complete paired reporting on a comparable CPU**,
plus preparation, fitting, rendering and verification. The estimate extrapolates
the short synthetic benchmark to 10,000 draws, up to 22 scored/anchor targets and
six analysis conditions; conditioning, extra binary summaries and SCC contention
may change runtime. A **4-CPU, 16-GB, 4-hour CPU job** is a reasonable initial
resource request. No GPU or 1.2-TB reconstruction envelope is justified.

The critical path is clinical adjudication, followed by exact panel/input lock,
one train/validation fit and fixed-test evaluation,
then paired reporting and figure QA. Scheduler wait and clinician turnaround
are not included in the computational estimate. The missing submission PDF
affects historical source comparison. Vendor emails establish the slide format
and deadline; permission for updated results and remaining export/publication
rules still require confirmation before final poster submission. These gaps do
not block scientific preparation.

## Maintained validation — 2026-09-09

The complete maintained `tests/` suite passed locally: **2,063 passed, 1 skipped**
in 117.66 seconds, using Python 3.12.3, NumPy 1.26.4, pandas 2.2.2,
scikit-learn 1.5.0 and isolated pytest 8.3.5, with umask 077 and four BLAS
threads. The sole warning is the expected duplicate-member synthetic tamper
fixture. Two legacy test fixtures now set their intended file modes explicitly;
the canary mock also accepts the existing typed runtime context. No C3
production implementation changed. The same two fixture files also passed under
umasks 077 and 022. The five PDF templates were rendered and visually checked;
their manifest binds all ten SVG/PDF artifacts. These are software and layout
checks, not project-model performance results.

## Completed SCC preparation — 2026-09-09

All six focused SCC test files passed: **86 passed**, exit status zero, under
the separately recorded SCC environment. The actual input adapter passed,
the three serialized split arrays were rehashed, and all 22 prepared targets'
ordered subject-study-label fingerprints replayed across train, validation and
test. The [current input funnel](current_input_funnel.md) records selected
4,530 (3,171/679/680), imaging-eligible 4,525 (3,168/678/679), and the exact five
prespecified no-cine exclusions. Observed selected LVEF before imaging was
2,836 (1,998/411/427); common LVEF was 2,833 (1,997/410/426), with 103 exact-40
labels (71/12/20).

All 21 candidates plus LVEF passed computational support and positive training-IQR
checks. Selected numeric candidate counts equaled valid-unit counts; there were
zero repeated numeric target rows after selection and zero incompatible numeric
rows among candidate21. These findings do not verify the native LVEF unit or
approve final clinical construct/aggregation rules. The nine technical
dispositions were published and the grouped panel/dependency draft prepared.
Eight clinical choices and the evidence-based grouped final review remain open.

| Owner-private evidence | SHA-256 |
|---|---|
| Owner instruction receipt | `eadccf524c778206b53ed28fce7f71e04de03ffd677d28a76e1e9b1faef3c650` |
| SCC environment receipt | `0315a5ab66e8825c7c0a5743bc975af3d520be5dc8d4137dd010b10a82e57bfc` |
| Analysis source receipt | `fc9beea169465d84974cd0589bf0db83dcbe52d90b80c05d7935ada4e1848bf3` |
| Model-independent inputs | `b82fe7d4a7c3f3cb8aed57038af409d7861aa1b09130292052f0c330cebae4de` |
| Aggregate identity/funnel replay | `501501b91069ff9252f0bfc98d102e1d61b18a493f91daa278aa6cd80b99ec01` |
| Nine technical decisions | `427fd59fe75b4985cb2556e94a6b949af1edeabe98282bac0e0eab1c2d28a975` |
| Grouped panel/dependency draft, pending approval | `4d72afcdfa9f6e707e712183c36c1ac6d7a486e4e2cc741b12c30821fb41c0dc` |
| SCC focused validation | `dcb15fbfcd59eda92e003485228f9dc97a9b0655aee3af41770b2434f7cb9035` |

At reconciliation, there were zero project-model fits, zero performance access,
no active preparation helper and no analysis `qsub` submission. The analysis
lock remains unsealed; no model freeze or fixed-test release has been created.
The next execution milestone is the clinically adjudicated panel and dependency
lock, followed by the already authorized train/validation stage. Source or
environment changes must be rebound before that execution; completed preparation
does not require repeating C3 reconstruction or its full preservation replay.
