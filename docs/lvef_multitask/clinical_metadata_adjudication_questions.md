# Restricted clinical metadata adjudication

Status: **SCC-only questionnaire generated; eight echocardiographer decisions and nine technical reviews remain open**.

The Phase 1D metadata audit completed successfully at commit `e97324a`. Its generated clinician form contains only issues classified `REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION`. The form includes restricted source metadata and therefore remains on SCC as:

`clinical_metadata_clinician_questionnaire_restricted.md`

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

Each is presented as an explicit multiple-choice decision beside the restricted project metadata. Lexically different descriptions alone cannot establish distinct constructs, and identical descriptions alone cannot establish duplicate values.

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

In particular, the mapping authority contains no exact `lvef` row. Candidate LVEF-adjacent rows cannot substitute for that authority, and LVEF method composition cannot be adjudicated until the technical mapping gap is resolved.

No issue is currently `LITERATURE_ANSWERABLE`, so the clinician should not generate an OpenEvidence query from this packet.

## Required signoff

The restricted record must retain:

- the packet checksum and commit;
- one selected response for each of the eight clinician questions;
- clinician initials and date;
- technical reviewer disposition for all nine technical issues;
- explicit exclusion rather than silent mapping for every unresolved item.

Until both clinician and technical signoff are complete, the raw-alias/unit review, clinical dependency registry, and task-panel gates remain open.
