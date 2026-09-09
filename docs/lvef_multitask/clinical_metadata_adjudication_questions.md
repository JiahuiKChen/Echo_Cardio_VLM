# Restricted clinical metadata adjudication

Historical Phase 1E workflow status: **the fixed SCC-only signoff workflow was prepared while eight echocardiographer decisions and nine technical reviews were still open**. The workflow description below is retained as history. The later [2026-09-09 readiness record](analysis_readiness_2026_09_09.md) records completed owner-relayed clinical adjudication, published technical/panel authorities and the submitted analysis; the original questionnaire and blank response remain unchanged.

The Phase 1D metadata audit completed successfully at commit `e97324a`. Phase 1E adds a fixed packet builder and response validator. The form includes restricted source metadata and therefore remains on SCC as:

`clinical_metadata_clinician_signoff_restricted.md`

Do not paste that form, its source rows, or completed responses into Git. Only a separately reviewed aggregate adjudication summary may be imported later.

## Questions requiring echocardiographer adjudication

The restricted form contains exactly these eight issue groups:

1. `ARCH_DIAM_LEVEL`
2. `ASCENDING_AORTA_CONVENTION`
3. `INF_LAT_THICKNESS_DEFINITION`
4. `IVC_DIAM_CONTEXT`
5. `LA_DIMEN_PLANE`
6. `MITRAL_E_FIELD_RELATIONSHIP`
7. `SINUS_DIAM_CONVENTION`
8. `TR_MMHG_DEFINITION`

Each is presented as an explicit multiple-choice decision beside the exact restricted project metadata. Every item includes an `UNRESOLVED_EXCLUDE` option, a required rationale, and the consequences for alias handling, target-family masking, and task scoring. Lexically different descriptions alone cannot establish distinct constructs, and identical descriptions alone cannot establish duplicate values.

## Questions not delegated to the clinician

The following nine issues require technical pipeline review and are intentionally excluded from the clinician questionnaire:

- `BSA_FORMULA_WEIGHT_AVAILABILITY`
- `DIMENSION_CM_MM_UNITS`
- `LVEDV_LVESV_FIELDS`
- `LVEF_ALIASES`
- `LVEF_METHOD_MIXTURE`
- `LV_MASS_RWT_FIELDS`
- `MITRAL_EA_EEPRIME_RATIO_FIELDS`
- `VELOCITY_MPS_CMPS_UNITS`
- `WALL_MOTION_FIELDS`

The mapping authority contains no exact `lvef` row because LVEF is governed separately by the exact raw target, numeric median aggregation within `(subject_id, measurement_id)`, the selected-study linkage, `build_lvef_still_manifest.py`, the historical manifest, and the passed label-provenance audit. A synthetic mapping row must not be created. This separate authority does not resolve LVEF method mixture or permit candidate aliases as predictors.

No issue is currently `LITERATURE_ANSWERABLE`, so the clinician should not generate an OpenEvidence query from this packet.

## Required signoff

The restricted record must retain:

- the packet SHA-256 and source commit;
- one selected response for each of the eight clinician questions;
- reviewer name or initials, role/expertise, and ISO signoff date;
- a rationale for every response;
- technical reviewer disposition for all nine technical issues;
- explicit exclusion rather than silent mapping for every unresolved item.

Until both clinician and technical signoff are complete, the raw-alias/unit review, clinical dependency registry, and task-panel gates remain open.
