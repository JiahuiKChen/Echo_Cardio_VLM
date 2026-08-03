# Restricted clinical metadata adjudication

Status: **question-bank authority only; no current clinician form exists**.

The clinician-facing form is generated mechanically on SCC by `scripts/lvef_multitask_clinical_metadata.py`. It contains only issues classified `REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION` after exact raw descriptions and units have been parsed. This prevents a clinician from being asked to resolve missing source metadata, alias equality requiring value comparison, or literature questions that should be handled elsewhere.

The generated restricted file is:

`clinical_metadata_clinician_questionnaire_restricted.md`

It may contain exact non-patient raw names, descriptions, units, and mapping sources and therefore must remain on SCC. Do not paste it into Git or the terminal transcript. The repository stores only the question-bank logic and aggregate counts by evidence type.

## Mechanical routing before clinician review

- Missing descriptions, missing/ambiguous units, unvalidated candidate mappings, indexing provenance, and BSA formula/weight lineage route to `REQUIRES_TECHNICAL_PIPELINE_REVIEW`.
- Equivalent mitral-E descriptions/units or other relationships requiring record-level equality checks route to `REQUIRES_VALUE_DISTRIBUTION_AUDIT`.
- Measurement-methodology evidence questions route to `LITERATURE_ANSWERABLE` only after dataset identity and units are resolved.
- Absent authority routes to `NOT_RESOLVABLE_FROM_AVAILABLE_DATA`.
- Only clinically ambiguous, nonempty project metadata routes to `REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION`.

## Clinician question bank

When triggered, the generator supplies explicit multiple-choice questions for:

- the pressure construct represented by `tr_mmhg`;
- whether `mv_peak_e` and `mitral_e_velocity` represent the same or incompatible acquisition/site constructs;
- the wall, phase, and method represented by `inf_lat_thickness`;
- the plane and timing represented by `la_dimen`;
- the named level represented by `arch_diam`;
- edge/timing conventions for `sinus_diam` and `ascending_aorta_diameter`;
- respiratory/ventilation context for `ivc_diam`;
- clinical harmonizability of explicitly documented mixed LVEF methods.

The generator includes a question only when the corresponding evidence classification requires it. Lexically different descriptions alone never prove two fields are distinct; identical descriptions alone never prove their values are duplicates.

## Signoff

The restricted form must record clinician initials/date and remain linked to its packet checksum. A technical reviewer must separately confirm that every missing or ambiguous source-metadata issue is closed or explicitly excluded. Until then, raw-alias/unit review, the clinical dependency registry, and task panels remain unlocked.
