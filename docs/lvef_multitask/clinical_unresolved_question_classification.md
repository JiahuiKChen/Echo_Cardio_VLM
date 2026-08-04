# Clinical metadata question classification

Status: **Phase 1D SCC packet complete and aggregate-safety validated; clinical and technical adjudication remain open**.

The final clinical metadata audit ran successfully at commit `e97324a`. It classified project-specific questions without importing OpenEvidence mappings, repairing identifiers, merging aliases, accessing patient values, or authorizing a task panel.

## Aggregate packet result

| Item | Result |
|---|---:|
| Source metadata rows | 188 |
| Source metadata columns | 9 |
| Restricted packet rows | 67 |
| Exact allowlisted targets requested | 30 |
| Exact allowlisted targets present | 29 |
| Missing exact target | `lvef` |
| Candidate canonical mappings outside the allowlist | 30 |
| Unresolved issues | 17 |
| Echocardiographer-adjudication issues | 8 |
| Technical-pipeline-review issues | 9 |
| Literature-answerable issues | 0 |
| Aggregate clinical safety gate | `PASS` |
| Targeted follow-up gate | `PASS_NO_PROMPT_REQUIRED` |

The missing `lvef` mapping is a project-metadata finding, not permission to promote one of the outside-allowlist candidates. All 30 outside-allowlist canonical candidates remain candidate-search rows only and are not registry authority.

## Authority and output boundary

`scripts/lvef_multitask_clinical_metadata.py` accepts only exact repository identifiers from `lvef` plus the 29 `legacy29` names. The restricted packet contains source metadata needed for adjudication and remains on SCC. It contains no patient values, patient/study identifiers, predictions, embeddings, or DICOM/clip content.

The Git-safe aggregate packet contains exactly:

1. `clinical_metadata_review_packet_manifest.json`
2. `clinical_metadata_schema_summary.json`
3. `clinical_metadata_ambiguity_counts.csv`
4. `clinical_metadata_unit_summary.csv`
5. `clinical_metadata_alias_summary.csv`
6. `clinical_metadata_safety_gate.json`

The separately gated follow-up packet contains only its generated-status document and safety gate. No raw source metadata or restricted questionnaire is copied into Git.

## Evidence-type rules

| Evidence type | Mechanical rule | Permitted next action |
|---|---|---|
| `RESOLVED_BY_PROJECT_METADATA` | Complete project metadata establishes presence/absence without requiring a clinical inference. | Record that project fact only; do not infer a formula or margin. |
| `LITERATURE_ANSWERABLE` | Dataset identity and units are resolved and only a measurement-methodology evidence question remains. | Generate a narrowly targeted evidence query. |
| `REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION` | Metadata are present but encode a clinically ambiguous construct. | Review the corresponding SCC-only multiple-choice question. |
| `REQUIRES_TECHNICAL_PIPELINE_REVIEW` | Mapping, description, unit, formula, or source lineage remains insufficient. | Resolve from project authorities; do not ask a clinician to guess. |
| `REQUIRES_VALUE_DISTRIBUTION_AUDIT` | Metadata suggest possible equivalence, but restricted values must be compared. | Run a model-independent restricted audit without performance access. |
| `NOT_RESOLVABLE_FROM_AVAILABLE_DATA` | Available authority cannot answer the question. | Fail closed. |

Clinical correlation alone never creates a formula edge. Lexical similarity or difference alone never proves that two fields are duplicates or distinct constructs.

## Phase 1D classifications

### Requires echocardiographer adjudication (8)

- `ARCH_DIAM_LEVEL`
- `ASCENDING_AORTA_CONVENTION`
- `INF_LAT_THICKNESS_DEFINITION`
- `IVC_DIAM_CONTEXT`
- `LA_DIMEN_PLANE`
- `MITRAL_E_FIELD_RELATIONSHIP`
- `SINUS_DIAM_CONVENTION`
- `TR_MMHG_DEFINITION`

These questions are present in the restricted clinician questionnaire. No clinical answer has yet been recorded in Git.

### Requires technical pipeline review (9)

- `BSA_FORMULA_WEIGHT_AVAILABILITY`
- `DIMENSION_CM_MM_UNITS`
- `LVEDV_LVESV_FIELDS`
- `LVEF_ALIASES` — seven restricted candidate rows; none is promoted to exact `lvef` authority.
- `LVEF_METHOD_MIXTURE` — no exact `lvef` mapping row is available, so method composition cannot be adjudicated from this packet.
- `LV_MASS_RWT_FIELDS`
- `MITRAL_EA_EEPRIME_RATIO_FIELDS`
- `VELOCITY_MPS_CMPS_UNITS`
- `WALL_MOTION_FIELDS`

### Resolved by project metadata (3)

- `INDEXED_MEASUREMENTS` — no candidate identified by the complete mapping search.
- `QUALITATIVE_LV_FUNCTION_FIELDS` — no candidate identified by the complete mapping search.
- `STROKE_VOLUME_CARDIAC_OUTPUT_FIELDS` — no candidate identified by the complete mapping search.

These are absence findings within the reviewed mapping authority, not claims that such concepts can never exist in another source or later data release.

## Unit and alias summary

- Twenty-eight targets have `SINGLE_KNOWN_UNIT` status.
- `ascending_aorta_diameter` has `MIXED_KNOWN_AND_UNKNOWN` unit status.
- `lvef` has `NO_SOURCE_ROWS` unit status.
- Twenty-eight targets have `ONE_ALIAS_COMPLETE` status.
- `ascending_aorta_diameter` has `MULTIPLE_ALIASES_DISTINCT_DESCRIPTIONS` status.
- `lvef` has `NO_SOURCE_ROWS` alias status.

These aggregate statuses do not resolve conversion validity, measurement convention, or alias equivalence. The corresponding clinical and technical gates remain open.

## Targeted evidence result

The classifier produced zero `LITERATURE_ANSWERABLE` issues. The separate follow-up safety gate passed with `PASS_NO_PROMPT_REQUIRED` and reason `ZERO_LITERATURE_ANSWERABLE_AMBIGUITIES`. No OpenEvidence prompt should be issued from this packet.

## Lock implication

The metadata audit and safety gates are complete, but the raw-alias/unit review, exact `lvef` mapping authority, clinical dependency registry, task panels, clinician signoff, and technical-review signoff remain open. The restricted clinician questionnaire must be completed on SCC before any aggregate adjudication is used to update dependency or panel authorities.
