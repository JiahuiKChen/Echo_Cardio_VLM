# Evidence-to-claim matrix

Status date: 2026-09-09. “Pending” reflects the absence of a released analysis artifact in this writing snapshot, not a failure of C3 or a conclusion about a parallel review's outcome.

| Proposed statement | Minimum evidence | Current writing status | Permitted wording / limit |
|---|---|---|---|
| Complete selected-cohort reconstruction | Completed C3 aggregate, 19 final receipts, derived closure/completion handoff | Available; authority hashes in [README](README.md) | Report reconstruction counts only; no prediction claim |
| Same cohort across three modalities | Exact ordered subject/study/label/split/multiplicity agreement and common-row hashes | New final-store audit required | Equal counts alone do not prove pairing |
| Exact LVEF label and `<40` definition | Exact-name source authority, units/aggregation, value equality, boundary counts | Historical label audit available; new binding required | Prior 2,833/426 and exact 40 counts are references until verified |
| Clinically coherent strict panel | Reconciled technical review; actual clinician decisions; production registry; units/masks; support; fixed order/hash | Use current readiness artifact | Do not promote provisional 21 or historical 29 |
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

Every populated claim should cite a machine-readable result key and bundle hash, not only a screenshot or copied number. Retain the historical evidence even when the revalidated estimate differs. New comparator hypotheses introduced after renewed test access are exploratory unless independently validated.
