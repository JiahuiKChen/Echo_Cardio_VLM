# Evidence-to-claim matrix

Status date: 2026-09-09. The [current input funnel](current_input_funnel.md) and SCC synthetic validation have passed. “Pending” below applies to clinical/panel approval or unreleased model results, not a failure of C3 or absence of the completed input audit.

| Proposed statement | Minimum evidence | Current writing status | Permitted wording / limit |
|---|---|---|---|
| Complete selected-cohort reconstruction | Completed C3 aggregate, 19 final receipts, derived closure/completion handoff | Available; authority hashes in [README](README.md) | Report reconstruction counts only; no prediction claim |
| Same prepared cohort across three modalities | Exact ordered subject/study/label/split/multiplicity agreement and common-row hashes | PASS: three split arrays rehashed; fingerprints for all 22 prepared targets across three splits replayed | Imaging eligibility 3,168/678/679 verified; final approved target/mask selection remains pending |
| Exact LVEF label and `<40` definition | Exact-name source authority, operational scale/aggregation, value equality, boundary counts | PASS current common-row/boundary audit: 2,833 labels, 1,997/410/426; exact-40 count 103, split 71/12/20 | Native LVEF unit declaration remains unverified; method composition remains unknown; label audit is not model-performance access |
| Clinically coherent strict panel | Reconciled technical review; actual clinician decisions; production registry; units/masks; support; fixed order/hash | Nine technical dispositions published; all 21 candidates plus LVEF pass support/IQR; eight clinical decisions and grouped final review pending | Do not promote the provisional 21 or historical 29 targets, or interpret unit compatibility as clinical aggregation approval |
| Frozen encoder with supervised downstream models | Checkpoint and encoder-state hash; no-gradient inference; frozen features; downstream fit manifests | C3 encoding established; fits pending | Never call fitted probes zero-shot |
| Quantitative image cues absent | Blinded audit of actual encoder-visible pixels and coverage limits | Not established by mask receipts | Say mask applied; state cue-retention uncertainty |
| Fusion lowers LVEF error | Frozen test predictions, exact paired cohort, paired MAE interval, full core Holm family | Pending | Distinguish numerical, statistical, and margin-based statements |
| Fusion improves the strict panel | Every locked task, shared subject bootstrap, no task dropping, paired macro contrasts, full core Holm | Pending | No favorable-subset substitution or silently reduced family |
| Fusion improves `<40` discrimination | Separate logistic model; event floors; paired AUROC contrast; secondary Holm family | Pending | Keep binary secondary to continuous anchor |
| Error difference is clinically important | Independent endpoint-specific evidence and appropriate estimand | Not established | LVEF margins are expert-inference research choices, not MCID |
| Practical equivalence under chosen margin | Entire paired interval inside ±delta, declared independently | Pending | Qualify with “under this research margin”; not clinical equivalence |
| Naturally missing labels can be recovered accurately | Independent ground truth for originally missing labels | Unavailable in this design | Limit to observed-label completion and simulated withholding |
| The model directly measures anatomy/flow | Appropriate pixel-localization/trace reference and direct measurement evaluation | Not evaluated | Report-label prediction only |
| Benefit for perioperative or critical-care decisions | Relevant clinical workflow/outcomes and prospective evaluation | Not evaluated | Future motivation, not demonstrated benefit |
| External or untouched validation | Independent cohort and exposure audit | Not present | Prespecified revalidation on a historically exposed split |
| Performance is robust to missingness | Locked withholding scenarios, fixed test rules, paired effects, support | Pending | Simulation does not resolve MNAR or deployment missingness |
| No demographic disparity / fairness | Powered design, valid coding, prespecified estimand and uncertainty | Not established | Descriptive subgroup estimates with suppression only |
| Native EchoPrime system is evaluated | Native attention/view/retrieval pipeline plus independent candidate corpus | Not evaluated | Mean-pooled encoder-only system is distinct |
| PanEcho comparison is fair | Frozen checkpoint/license/task schema; matched inputs/subjects/budget; disclosed pretraining; locked analysis | Feasibility only | Separate representation comparison from native-head comparison |
| Updated ASA results may replace accepted numbers | Current organizer/vendor permission and versioned provenance | Unverified | Draft updated panels; do not export or rewrite accepted authority |

Every populated model-performance claim should cite a machine-readable result key and validated result-bundle hash, not only a screenshot or copied number. Model-independent preparation counts instead cite the corresponding input-receipt or aggregate-funnel key and hash below; they do not require a fitted-result bundle. Retain the historical evidence even when the revalidated estimate differs. New comparator hypotheses introduced after renewed test access are exploratory unless independently validated.

Current preparation evidence: analysis source `073d3883fc54c4041a043efce850ecfeca07890a`; input receipt SHA-256 `b82fe7d4a7c3f3cb8aed57038af409d7861aa1b09130292052f0c330cebae4de`; aggregate identity/funnel SHA-256 `501501b91069ff9252f0bfc98d102e1d61b18a493f91daa278aa6cd80b99ec01`. The [execution record](execution_runbook.md) binds owner, environment, source, technical review and the 86 passing SCC tests. These preparations involved zero project-model fits and zero performance access. The [readiness record](../analysis_readiness_2026_09_09.md) governs the remaining clinical and panel decisions.
