# JDIM Audit Clip Default Preset V1

Protocol identifier: `JDIM_AUDIT_CLIP_DEFAULT_PRESET_V1`

This interface-efficiency preset applies only to new primary, new secondary, and
owner-authorized restart study-role events created after the recorded V3.1
cutover. Existing and prior-protocol events remain unchanged.

The preset displays these initial clip-form values:

- `acquisition_content_type`: `2d_b_mode`
- `waveform_or_measurement_tracing`: `no`
- `calipers`: `no`
- `visible_text`: `yes`
- `visible_numeric_value`: `yes`
- `lvot_vti_specific_label`: `no`
- `tapse_specific_label`: `no`
- `candidate_target_value_present`: `no`
- Candidate value, unit, displayed name, and precision: blank
- `reader_confidence`: `high`
- `restricted_notes`: blank

No source-only, study-level, adjudication, administrative, reviewer-identity,
role, or not-assessable-reason field is initialized. Displayed defaults do not
count as annotations. Every clip requires an attributable reviewer to inspect
the designated panel and explicitly confirm or change the displayed responses.
The preset uses no model output, report-label value, target, prediction, or prior
reviewer answer. Possible anchoring from common starting values should be
acknowledged when the audit is reported.

Future supplementary-methods insertion, not added during deployment:

> To reduce repetitive data entry, clip forms were initialized with a common
> set of starting values; each clip nevertheless remained unreviewed until a
> physician inspected the designated panel and explicitly confirmed or changed
> every displayed response.
