-- Upload outputs/mimic_echo_subset/<stage>/selected_note_ids.csv into a working BigQuery table first.
-- Minimal required columns in that uploaded table:
--   study_id
--   subject_id
--   note_id
--
-- This query intentionally uses only note_id + text because those are documented on the
-- MIMIC-IV-Note side. Optional refinement by note_seq or note_charttime should wait until
-- the exact BigQuery schema in your project is verified.
--
-- Replace:
--   YOUR_PROJECT.YOUR_WORK_DATASET.selected_echo_note_ids
--   YOUR_NOTE_DATASET.radiology
--
-- Inference from official docs:
--   echocardiography reports should map into the MIMIC-IV-Note radiology domain because
--   MIMIC-IV-Note radiology reports span multiple imaging modalities including ultrasound.

SELECT
  e.study_id,
  e.subject_id,
  e.note_id,
  r.text AS note_text
FROM `YOUR_PROJECT.YOUR_WORK_DATASET.selected_echo_note_ids` AS e
LEFT JOIN `YOUR_NOTE_DATASET.radiology` AS r
  ON CAST(e.note_id AS STRING) = CAST(r.note_id AS STRING)
ORDER BY e.study_id;
