# ASA five-minute narrative and two-minute Q&A

Draft for A1122, preserving the accepted title and recorded five-minute/two-minute format. This is a rehearsal script, not an exported poster or permission to change accepted results. Resolve the [conference requirements](conference_and_submission_sources.md) before submission. Bracketed result fields require the validated statistical bundle.

## Narrative, approximately five minutes

**0:00–0:40 — question.**

“Our question is whether cine-derived information adds value to available structured echocardiographic measurements when predicting an observed report label. We compare vision-only, structured-only, and early fusion. LVEF is our primary continuous anchor, followed by a clinically reviewed measurement panel. This is report-label completion research. We are not evaluating a system that draws calipers, replaces a clinician, or recovers the unknown truth of naturally missing measurements.”

**0:40–1:20 — cohort.**

“We selected one study per subject in MIMIC-IV-ECHO. The completed reconstruction includes 4,530 selected studies, with study representations for 4,525; five prespecified studies had no eligible cine. We exclude those five from all primary modality comparisons. Within each target, every modality uses exactly the same subjects and observed target values. The final LVEF analysis includes `[N_COMMON]` subjects, with `[N_TEST]` in the test split. The flow diagram separates selection, imaging eligibility, and target-label availability.”

**1:20–2:10 — what the models see.**

“We use a frozen EchoPrime video encoder and deterministic study mean pooling, then fit supervised downstream models. We do not use the complete native retrieval system, and these fitted models are not zero-shot. For structured inputs, we remove the target, verified aliases, and prohibited formula or family information before feature selection, imputation, scaling, and missingness indicators. This order matters: even an indicator for a prohibited measurement can preserve a shortcut. The diagram shows the shared training-only transformations and our mandatory comparison without indicators.”

**2:10–2:50 — design and inference.**

“Ridge regression models continuous labels. Separately fitted logistic regression predicts LVEF strictly below 40 percent; at-or-below 40 and below 50 are sensitivities. Hyperparameters are selected on validation data, and the primary models are not refit using validation subjects. We use 10,000 paired subject bootstrap draws with fixed models. For the panel, each draw shares the same subject multiplicities across tasks and modalities. The four core fusion comparisons are evaluated together using Holm adjustment.”

**2:50–3:50 — validated results only.**

“For continuous LVEF, vision, structured, and fusion MAE were `[V]`, `[S]`, and `[F]` EF points. Fusion minus vision was `[D_FV]`, with paired 95 percent interval `[CI_FV]`; fusion minus structured was `[D_FS, CI_FS]`. `[VALIDATED_DIRECTION_AND_HOLM_INTERPRETATION]`. Across all `[K]` locked panel targets, `[COMPLETE_PANEL_RESULT]`. The task plot shows native-unit errors and denominators for every target, including `[VALIDATED_UNFAVORABLE_OR_INDETERMINATE_PATTERN]`. `[NO_INDICATOR_RESULT]` describes sensitivity to structured missingness patterns.”

Do not rehearse invented values. If the analysis bundle is not ready, replace this whole minute with: “Reconstruction is complete; predictive revalidation has not yet been released. These panels show the prespecified comparisons and will be populated only from the locked, validated analysis.” If the core panel family cannot activate, do not announce a smaller family as confirmatory.

**3:50–4:35 — interpretation.**

“A lower statistical error does not automatically mean clinical benefit. Our LVEF research margins are expert-inference thresholds, not established minimal clinically important differences. For other measurements without an independently locked margin, we report native errors and paired intervals without win/tie/loss labels. We also distinguish observed-label prediction from experiments that deliberately withhold known structured fields.”

**4:35–5:00 — limitations and next step.**

“This is a retrospective single-source study on a historically exposed test split. Image preprocessing applies sector masking, but it does not prove that all numerical overlays, calipers, or traces are absent; a blinded content audit is proposed. The immediate next step is `[VALIDATED_CURRENT_MILESTONE]`. External validation and prospective workflow evaluation are needed before clinical claims. Comparator expansion remains a separate study question.”

## Two-minute discussion preparation

Use two or three responses as needed, roughly 20–35 seconds each. Answer from the completed bundle; do not imply that planned checks already passed.

| Likely question | Concise response |
|---|---|
| Is the image model reading EF or measurement text? | “The mask removes pixels outside an occupancy/motion-derived sector, but no semantic detector guarantees removal of in-sector text or calipers. We state that limitation and propose a blinded audit of the actual 16 encoder-visible frames, with content-restricted sensitivity where membership is validated.” |
| Why not call this zero-shot EchoPrime? | “The encoder is frozen, but our Ridge/logistic models are supervised on this cohort. Mean pooling also differs from native EchoPrime's view-informed retrieval pipeline.” |
| Why did the results or panel change from the accepted abstract? | “We preserve that analysis unchanged. Revalidation binds reconstructed features, identical modality rows, stronger masks, clinical panel decisions, and paired inference. The dated comparison identifies those changes; it does not overwrite the historical result.” |
| Is this an independent test set? | “No. The historical split's results were previously exposed. The new rules were recorded before renewed access, and we retain that split transparently as revalidation rather than manufacture an untouched-test claim.” |
| Does an error reduction help patient management? | “This design measures report-label agreement, not decisions or outcomes. The 1-point LVEF margin is a research convention, not an established MCID. Clinical benefit requires separate prospective evaluation.” |
| Are missing measurements accurately imputed? | “Only observed labels supply reference truth. Deliberate withholding tests a defined simulation; it cannot validate the unknown values of naturally unreported measurements.” |
| Why exclude five studies from structured-only? | “The primary scientific question is paired modality comparison on identical imaging-eligible subjects. A structured-only analysis retaining all available subjects is reported separately.” |
| Why no PanEcho or EchoNet baseline yet? | “The immediate comparison isolates modalities under one frozen-representation pipeline. A separate proposal distinguishes matched frozen features from native pretrained heads, with independent input selection, licenses, checkpoint provenance, and retrieval independence established before execution.” |
| What about panel tasks without clinical margins? | “We still show native-unit error, denominators, and paired uncertainty. We withhold margin-based classifications until an independent task-specific margin exists.” |

## Poster layout draft

Use a clear reading order: question → cohort flow → masking/model diagram → LVEF paired effects → complete task/family results → missingness and limitations. Put the historical-versus-revalidated explanation near the result date, not in an unreadable footnote. Keep all task estimates available in the poster or a permitted linked supplement. Do not set canvas dimensions, page count, or media settings until current vendor instructions are verified. No patient images or identifiers belong in these draft assets.
