# Phase 1E clinical-metadata adjudication findings

Status: **LVEF label authority resolved separately; restricted technical review and human echocardiographer signoff remain closed gates**.

This record is model-independent. It does not use predictions, embeddings, confirmatory performance, or historical model rankings to define a target, relationship, unit, or task panel.

## LVEF authority ruling

The absence of `lvef` from the 188-row raw-to-canonical measurement map is expected and does not require a synthetic mapping row. LVEF has a separate, reproducible label authority:

1. the structured-measurement export is filtered by the exact, case-sensitive raw target name `lvef`;
2. `result` is parsed as numeric and analyzed on the historical EF-percentage-point scale;
3. repeated exact-name rows are aggregated by the median within `(subject_id, measurement_id)`;
4. the result is joined to the one-study-per-subject selected-study authority by `(subject_id, measurement_id)`; and
5. `build_lvef_still_manifest.py`, the historical `lvef_still_manifest.csv`, and the passed label-provenance audit provide independent implementation and reconciliation evidence.

The historical primary binary endpoint remains exactly `lvef < 40`. The prespecified secondary sensitivities remain `lvef <= 40` and `lvef < 50`; none may replace the primary endpoint based on model results. The pre-performance exact-threshold audit recorded:

| Cohort scope | Split | Numeric LVEF | Exactly 40 |
|---|---|---:|---:|
| Selected pre-imaging | All | 2,836 | 103 |
| Selected pre-imaging | Train | 1,998 | 71 |
| Selected pre-imaging | Validation | 411 | 12 |
| Selected pre-imaging | Test | 427 | 20 |
| Primary common imaging-eligible | All | 2,833 | 103 |
| Primary common imaging-eligible | Train | 1,997 | 71 |
| Primary common imaging-eligible | Validation | 410 | 12 |
| Primary common imaging-eligible | Test | 426 | 20 |

This ruling establishes label lineage, aggregation, the historical analytical scale, and endpoint inequalities. It does **not** prove a native source-unit declaration or establish whether the source combines visual, Simpson biplane, Teichholz, three-dimensional, contrast-enhanced, or other LVEF methods. If no method variable exists, the correct disposition is `METHOD_UNSPECIFIED_UNSTRATIFIABLE`, not an assertion that mixed methods were demonstrated. Candidate EF aliases or method-specific exports are not promoted to label authority and must be excluded from LVEF predictors until the restricted alias review is signed off.

## Nine technical questions

`scripts/audit_lvef_multitask_technical_metadata.py` now prepares the direct SCC evidence needed for exactly these issues:

- `BSA_FORMULA_WEIGHT_AVAILABILITY`
- `DIMENSION_CM_MM_UNITS`
- `LVEDV_LVESV_FIELDS`
- `LVEF_ALIASES`
- `LVEF_METHOD_MIXTURE`
- `LV_MASS_RWT_FIELDS`
- `MITRAL_EA_EEPRIME_RATIO_FIELDS`
- `VELOCITY_MPS_CMPS_UNITS`
- `WALL_MOTION_FIELDS`

The audit requires the checksum-locked 188-row raw-to-canonical mapping as well as the restricted clinical review rows and selected-cohort structured measurements. It deterministically regenerates the 67-row filtered packet from the complete mapping and fails before adjudication if the two do not match. It retains original value strings, parsed numeric values, optional row units/descriptions, and qualitative categories; applies the training split before every value relationship; reconciles the complete mapping universe; and computes prespecified same-report scale/equality diagnostics for candidate aliases. Those comparisons are diagnostic only and cannot establish an alias from correlation. Mapping-level absence claims are limited to the complete mapping and do not establish absence from source-generation lineage. All raw names, descriptions, units, values, category profiles, pair diagnostics, paths, and identifiers stay restricted. The Git-safe summary contains only issue identifiers, counts, evidence tiers, dispositions, confidence, and panel consequences. It does not read predictions or compute performance.

All nine issues remain blocked until the enhanced SCC audit is executed and scientifically reviewed. `LVEF_METHOD_MIXTURE` remains `PENDING_COMPLETE_LVEF_METHOD_LINEAGE_REVIEW` unless complete source-generation lineage, not merely a zero mapping match, establishes that method metadata are unavailable. Only then may `METHOD_UNSPECIFIED_UNSTRATIFIABLE` be recorded; that disposition still does not demonstrate mixed methods. LVEDV and LVESV deterministically reconstruct a volume-derived EF only when the paired volumes share the required method and beat, and equivalence to the project LVEF target additionally requires shared target provenance. Segment-level wall-motion findings must be separated from wall-motion score/index and global summaries: the former may be clinical correlates, whereas the latter may be deterministic aggregations or strong systolic shortcuts. Global qualitative LV-function or EF-category exports remain a separate possible target-derived shortcut class. LV mass, RWT, and indexed mass are separate dependency sets, and BSA belongs only to the indexed pathway.

## Eight echocardiographer questions

`scripts/build_lvef_clinician_signoff_packet.py` creates a fixed eight-question restricted form for:

- `ARCH_DIAM_LEVEL`
- `ASCENDING_AORTA_CONVENTION`
- `INF_LAT_THICKNESS_DEFINITION`
- `IVC_DIAM_CONTEXT`
- `LA_DIMEN_PLANE`
- `MITRAL_E_FIELD_RELATIONSHIP`
- `SINUS_DIAM_CONVENTION`
- `TR_MMHG_DEFINITION`

Each item displays its exact project canonical name, restricted raw description, recorded and normalized units, mapping source, explicit multiple-choice options, consequences for aliases/masks/scoring, a required rationale, and `UNRESOLVED_EXCLUDE`. The generated response template binds to the packet's SHA-256. The validator requires reviewer name or initials, echocardiographic role/expertise, ISO date, all eight unique responses, allowed choices, and a rationale for every choice.

The packet is ready for restricted generation and quality control, but human signoff has not been completed. A physician or echocardiographer with appropriate measurement expertise must make the final decisions. Wording alone cannot establish value equivalence.

## Export boundary and panel consequence

The packet, responses, raw descriptions, aliases, units, and train-only distributions remain on SCC. Only the 17-row aggregate template in `clinical_metadata_adjudication_summary_template.csv` may be populated for Git after separate export review. No strict, family-masked, or pragmatic panel is final until all nine technical questions and eight clinical questions are dispositioned. `UNRESOLVED_EXCLUDE` is a valid fail-closed resolution and must not be silently converted into a merge or scored target.

No new OpenEvidence prompt is justified at this stage. Preserve `NOT_GENERATED_ZERO_LITERATURE_ANSWERABLE_AMBIGUITIES` unless completed project metadata and clinician review newly isolate a genuinely literature-answerable question.
