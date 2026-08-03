# Clinical metadata question classification

Status: **mechanical framework implemented; project decisions await the SCC metadata packet**.

This framework separates questions that project metadata can answer from questions that require clinical judgment, technical lineage review, a value-distribution audit, or targeted literature evidence. It does not infer a field from an OpenEvidence suggestion, repair a corrupted identifier, merge aliases, or authorize a target panel.

## Authority and output boundary

`scripts/lvef_multitask_clinical_metadata.py` accepts only exact repository canonical identifiers from `lvef` plus the 29 `legacy29` names. A source row mapped outside that allowlist may enter the **restricted** candidate-search packet, but is marked `SOURCE_CANDIDATE_NOT_ALLOWLISTED` and cannot become registry authority.

The restricted packet contains exact raw names, descriptions, canonical mappings, mapping sources, native and normalized units, unit categories, and candidate flags. It never contains patient values, identifiers, DICOM/clip locators, predictions, or embeddings. The required aggregate packet contains exactly:

1. `clinical_metadata_review_packet_manifest.json`
2. `clinical_metadata_schema_summary.json`
3. `clinical_metadata_ambiguity_counts.csv`
4. `clinical_metadata_unit_summary.csv`
5. `clinical_metadata_alias_summary.csv`
6. `clinical_metadata_safety_gate.json`

The aggregate CSVs expose only allowlisted target names, issue identifiers, counts, booleans, status codes, and unit-family classifications. They do not expose raw names, descriptions, mapping sources, or unit strings.

An optional, separately gated follow-up directory contains only:

- `openevidence_targeted_followup_prompt_generated.md`
- `openevidence_targeted_followup_safety_gate.json`

Keeping these two files outside the six-file metadata aggregate inventory makes the packet contract auditable.

## Evidence-type rules

Every issue receives exactly one of these labels:

| Evidence type | Mechanical rule | Permitted next action |
|---|---|---|
| `RESOLVED_BY_PROJECT_METADATA` | All contributing aliases have sufficiently explicit, mutually compatible metadata, or a complete mapping search finds no candidate field. | Record the project fact. This does not establish a clinical formula or a margin. |
| `LITERATURE_ANSWERABLE` | Dataset identity is already resolved for every alias, units are known and coherent, and only a measurement-methodology or construct-specific evidence question remains. | A narrowly targeted evidence query may be generated. |
| `REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION` | Descriptions are present but encode clinically ambiguous or mixed constructs that a technical parser cannot choose between. | Ask one mechanically selected multiple-choice question. |
| `REQUIRES_TECHNICAL_PIPELINE_REVIEW` | A description/unit is missing, a regex candidate has not been mapped, indexing or formula provenance is incomplete, or source lineage is otherwise insufficient. | Resolve from the source dictionary or pipeline; do not ask a clinician to guess. |
| `REQUIRES_VALUE_DISTRIBUTION_AUDIT` | Two fields have equivalent descriptions and units but equality/convertibility of their values has not been established. | Run a restricted, model-independent overlap/distribution audit. No performance metric may be read. |
| `NOT_RESOLVABLE_FROM_AVAILABLE_DATA` | The relevant target/source rows are absent or the available authority cannot answer the question. | Keep the relationship unresolved and fail closed. |

Clinical correlation alone never creates a formula edge. A candidate-name pattern establishes only that a restricted row needs review, not that the field exists as a usable predictor or target.

## Prespecified issue coverage

The classifier must emit issue rows covering, at minimum:

- the meaning of `tr_mmhg`;
- `mitral_e_velocity` versus `mv_peak_e`;
- the definitions of `inf_lat_thickness`, `la_dimen`, `arch_diam`, `sinus_diam`, `ascending_aorta_diameter`, and `ivc_diam`;
- LVEF methods and candidate aliases;
- candidate LVEDV/LVESV, qualitative LV-function, wall-motion, mitral-ratio, stroke-volume/cardiac-output, LV-mass/RWT, and indexed fields;
- BSA formula and weight availability;
- dimension cm/mm and velocity m/s versus cm/s ambiguity.

Candidate fields outside the canonical allowlist remain restricted search concepts. Presence triggers technical mapping review; absence is called resolved only when the supplied mapping is the complete project mapping.

## Clinician questionnaire rule

The clinician questionnaire is generated inside the restricted output directory after classification. It includes only issues labeled `REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION`. Raw metadata associated with technical, literature, value-distribution, resolved, or unavailable issues are not placed on the clinician form.

No static all-purpose questionnaire is a lock authority. The generated form, its packet checksum, clinician initials/date, and technical-review signoff must remain together on SCC until an aggregate-safe adjudication summary is prepared.

## Targeted evidence rule

Only `LITERATURE_ANSWERABLE` rows can enter the follow-up generator. It:

- preserves the exact allowlisted target identifier;
- omits raw names and mapping sources;
- screens descriptions and units for identifier/locator patterns and control characters;
- requests claim-level mapping to professional guidelines, consensus statements, or primary measurement-methodology/reproducibility studies;
- requires DOI/PMID where available and `UNRESOLVED` instead of guessing;
- separates guideline facts, deterministic formula inference, and expert inference.

If no literature-answerable row remains, the generator records `NOT_GENERATED_ZERO_LITERATURE_ANSWERABLE_AMBIGUITIES`. If the screen fails, it records `NOT_GENERATED_SAFETY_GATE_FAILED`; it does not emit the unsafe metadata in the failure document.

## Current lock implication

The Phase 1C SCC clinical block did not run, so no project-specific evidence classification, clinician questionnaire, or copy-ready targeted prompt currently exists. The raw-alias/unit gate, dependency-registry gate, task-panel gate, and clinician-signoff gate remain closed until the supported SCC interpreter produces and validates this packet.
