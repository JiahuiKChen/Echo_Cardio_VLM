"""Restricted, blinded audit interface generation and checkpoint storage."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .audit import CONTENT_TYPES, PRESENCE_VALUES, READER_CONFIDENCE, STUDY_OUTCOMES
from .phase2i import EVIDENCE_TIERS
from .safety import (
    BLOCKED_UNSAFE_OUTPUT,
    Tier1BlockedError,
    require_columns,
    require_restricted_destination,
    sha256_file,
    write_json,
)


AUDIT_INTERFACE_READY = "AUDIT_INTERFACE_READY"
READY_FOR_BLINDED_HUMAN_AUDIT = "READY_FOR_BLINDED_HUMAN_AUDIT"
READER_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
PROHIBITED_READER_FIELDS = {
    "target",
    "target_name",
    "target_value",
    "split",
    "prediction",
    "residual",
    "filename",
    "source_filename",
    "study_id",
    "subject_id",
    "dicom_filepath",
    "source_path",
    "processed_path",
}
READER_VISIBLE_FIELDS = (
    "audit_id",
    "clip_audit_id",
    "review_order",
    "evidence_tier",
    "source_media_id",
    "model_input_media_id",
    "model_input_verified",
    "source_only",
)
CLIP_PRESENCE_FIELDS = (
    "waveform_or_tracing",
    "calipers",
    "contour_or_measurement_trace",
    "visible_text",
    "visible_numeric_value",
    "visible_unit",
    "visible_measurement_name",
    "lvot_vti_specific_label",
    "tapse_specific_label",
    "candidate_target_value_present",
)
CLIP_FREE_TEXT_FIELDS = (
    "candidate_target_value",
    "visible_unit_text",
    "visible_measurement_name_text",
    "display_precision",
    "restricted_notes",
)
CLIP_ANNOTATION_FIELDS = {
    "acquisition_content_type",
    *CLIP_PRESENCE_FIELDS,
    *CLIP_FREE_TEXT_FIELDS,
    "reader_confidence",
}
STUDY_ANNOTATION_FIELDS = {*STUDY_OUTCOMES, "reader_confidence", "restricted_notes"}


@dataclass(frozen=True)
class InterfacePackageResult:
    output_root: Path
    summary: dict[str, Any]


class CheckpointStore:
    """Atomic restricted checkpoints with independent reader namespaces and locks."""

    def __init__(self, root: Path):
        self.root = require_restricted_destination(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    @staticmethod
    def _reader_id(value: str) -> str:
        reader = str(value).strip()
        if not READER_ID_PATTERN.fullmatch(reader):
            raise ValueError("reader_id must contain only letters, numbers, dot, underscore, or hyphen")
        return reader

    def _reader_root(self, reader_id: str) -> Path:
        reader = self._reader_id(reader_id)
        path = (self.root / reader).resolve()
        if self.root.resolve() not in path.parents:
            raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "reader checkpoint escaped restricted root")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        return path

    def is_locked(self, reader_id: str) -> bool:
        return (self._reader_root(reader_id) / "LOCKED.json").is_file()

    def load(self, reader_id: str) -> dict[str, Any]:
        root = self._reader_root(reader_id)
        checkpoint = root / "checkpoint.json"
        if not checkpoint.is_file():
            return {"reader_id": self._reader_id(reader_id), "annotations": {}, "locked": False}
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        payload["locked"] = self.is_locked(reader_id)
        return payload

    def save(self, reader_id: str, payload: Mapping[str, Any]) -> Path:
        root = self._reader_root(reader_id)
        if self.is_locked(reader_id):
            raise PermissionError("reader annotations are locked")
        annotations = payload.get("annotations", {})
        if not isinstance(annotations, Mapping) or set(annotations) - {"studies", "clips"}:
            raise ValueError("checkpoint annotations must contain only studies and clips")
        studies = self._validated_annotation_group(
            annotations.get("studies", {}), STUDY_ANNOTATION_FIELDS, "study"
        )
        clips = self._validated_annotation_group(
            annotations.get("clips", {}), CLIP_ANNOTATION_FIELDS, "clip"
        )
        clean = {
            "reader_id": self._reader_id(reader_id),
            "annotations": {"studies": studies, "clips": clips},
        }
        destination = root / "checkpoint.json"
        handle, temporary_name = tempfile.mkstemp(prefix=".checkpoint-", suffix=".json", dir=root)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(clean, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
            os.chmod(destination, 0o600)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return destination

    @staticmethod
    def _validated_annotation_group(
        group: Any,
        allowed_fields: set[str],
        label: str,
    ) -> dict[str, dict[str, str]]:
        if not isinstance(group, Mapping):
            raise ValueError(f"{label} annotations must be an object")
        clean: dict[str, dict[str, str]] = {}
        for raw_identifier, raw_record in group.items():
            identifier = str(raw_identifier)
            if not READER_ID_PATTERN.fullmatch(identifier):
                raise ValueError(f"invalid {label} audit identifier")
            if not isinstance(raw_record, Mapping):
                raise ValueError(f"{label} annotation must be an object")
            unknown = sorted(set(raw_record) - allowed_fields)
            if unknown:
                raise ValueError(f"{label} annotation contains unsupported fields: {unknown}")
            record: dict[str, str] = {}
            for field, raw_value in raw_record.items():
                value = str(raw_value).strip()
                if field in STUDY_OUTCOMES or field in CLIP_PRESENCE_FIELDS:
                    if value and value not in PRESENCE_VALUES:
                        raise ValueError(f"invalid presence value for {field}")
                elif field == "acquisition_content_type":
                    if value and value not in CONTENT_TYPES:
                        raise ValueError("invalid acquisition content type")
                elif field == "reader_confidence":
                    if value and value not in READER_CONFIDENCE:
                        raise ValueError("invalid reader confidence")
                elif len(value) > 500 or any(
                    ord(character) < 32 and character not in "\t\n" for character in value
                ):
                    raise ValueError(f"invalid restricted text for {field}")
                record[str(field)] = value
            clean[identifier] = record
        return clean

    def lock(self, reader_id: str) -> Path:
        root = self._reader_root(reader_id)
        checkpoint = root / "checkpoint.json"
        if not checkpoint.is_file():
            raise FileNotFoundError("cannot lock before a checkpoint exists")
        destination = root / "LOCKED.json"
        if destination.exists():
            raise FileExistsError("reader annotations are already locked")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "status": "ANNOTATIONS_LOCKED",
                    "reader_id": self._reader_id(reader_id),
                    "checkpoint_sha256": sha256_file(checkpoint),
                },
                stream,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
        return destination


def reader_visible_record(row: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(row)
    leaked = sorted(PROHIBITED_READER_FIELDS.intersection(values))
    if leaked:
        for field in leaked:
            values.pop(field, None)
    record = {field: values.get(field, "") for field in READER_VISIBLE_FIELDS}
    tier = str(record["evidence_tier"])
    if tier not in EVIDENCE_TIERS:
        raise ValueError(f"unknown evidence tier: {tier}")
    record["model_input_verified"] = str(record["model_input_verified"]).lower() in {
        "true",
        "1",
        "yes",
    }
    record["source_only"] = str(record["source_only"]).lower() in {"true", "1", "yes"}
    return record


def _interface_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Blinded Echocardiography Input Audit</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header><strong>Blinded input-content audit</strong><span id="save-state">Loading</span></header>
  <main>
    <aside>
      <div id="study-id"></div>
      <div class="counter" id="study-counter"></div>
      <button id="previous-study" aria-label="Previous study">&#8592;</button>
      <button id="next-study" aria-label="Next study">&#8594;</button>
      <button id="lock-review">Lock review</button>
    </aside>
    <section>
      <div class="clip-nav">
        <button id="previous-clip" aria-label="Previous clip">&#8249;</button>
        <span id="clip-counter"></span>
        <button id="next-clip" aria-label="Next clip">&#8250;</button>
      </div>
      <div id="tier" class="tier"></div>
      <div class="views">
        <figure id="source-panel"><figcaption>Source acquisition</figcaption><img id="source-image" draggable="false"></figure>
        <figure id="model-panel"><figcaption id="model-caption">Model-input view</figcaption><img id="model-image" draggable="false"></figure>
      </div>
      <form id="clip-form" autocomplete="off">
        <fieldset><legend>Modality/content</legend><select name="acquisition_content_type">
          <option value="">Unreviewed</option><option value="2d_b_mode">2D B-mode</option>
          <option value="color_doppler">Color Doppler</option><option value="pulsed_wave_spectral_doppler">Pulsed-wave spectral Doppler</option>
          <option value="continuous_wave_spectral_doppler">Continuous-wave spectral Doppler</option><option value="tissue_doppler">Tissue Doppler</option>
          <option value="m_mode">M-mode</option><option value="mixed">Mixed</option><option value="other">Other</option>
          <option value="uncertain">Uncertain</option><option value="not_assessable">Not assessable</option>
        </select></fieldset>
        <fieldset id="presence-fields"><legend>Visible content</legend></fieldset>
        <fieldset><legend>Candidate displayed value (transcribe only when present)</legend>
          <label>Value<input name="candidate_target_value" maxlength="64"></label>
          <label>Unit<input name="visible_unit_text" maxlength="64"></label>
          <label>Displayed name<input name="visible_measurement_name_text" maxlength="120"></label>
          <label>Precision<input name="display_precision" maxlength="64"></label>
        </fieldset>
        <label>Reader confidence<select name="reader_confidence"><option value="">Unreviewed</option><option>high</option><option>moderate</option><option>low</option><option>not_assessable</option></select></label>
      </form>
      <form id="study-form" autocomplete="off"><fieldset><legend>Study summary</legend><div id="study-fields"></div></fieldset></form>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""


def _interface_css() -> str:
    return """*{box-sizing:border-box}body{margin:0;font:14px Arial,sans-serif;color:#171717;background:#f5f6f7}header{height:48px;padding:0 18px;display:flex;align-items:center;justify-content:space-between;background:#fff;border-bottom:1px solid #bbb}main{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 48px)}aside{padding:16px;border-right:1px solid #bbb;background:#fff}button,select,input{min-height:34px;margin:4px;padding:5px 8px}section{padding:14px;min-width:0}.clip-nav{display:flex;align-items:center;justify-content:center}.tier{font-weight:700;margin:6px 0}.views{display:grid;grid-template-columns:1fr 1fr;gap:10px}.views figure{margin:0;background:#fff;border:1px solid #aaa;padding:8px}.views img{display:block;width:100%;max-height:58vh;object-fit:contain;background:#000}.source-only #model-panel{display:none}.source-only .views{grid-template-columns:1fr}fieldset{border:1px solid #aaa;margin:10px 0;padding:10px;background:#fff}label{display:inline-flex;gap:4px;align-items:center;margin:4px 10px 4px 0}.counter{margin:8px 0;color:#555}@media(max-width:850px){main{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid #bbb}.views{grid-template-columns:1fr}}
"""


def _interface_js() -> str:
    return """'use strict';
const presence=['waveform_or_tracing','calipers','contour_or_measurement_trace','visible_text','visible_numeric_value','visible_unit','visible_measurement_name','lvot_vti_specific_label','tapse_specific_label','candidate_target_value_present'];
const studyFields=['spectral_doppler_present','m_mode_present','caliper_or_trace_present','visible_numeric_value_present','target_specific_label_present','candidate_target_value_present'];
let data={studies:[]}, state={annotations:{studies:{},clips:{}}}, si=0, ci=0, locked=false;
const choices='<option value="">Unreviewed</option><option value="yes">Yes</option><option value="no">No</option><option value="uncertain">Uncertain</option><option value="not_assessable">Not assessable</option>';
function fields(root,names){root.innerHTML='';names.forEach(n=>{const l=document.createElement('label');l.textContent=n.replaceAll('_',' ');const s=document.createElement('select');s.name=n;s.innerHTML=choices;l.appendChild(s);root.appendChild(l);});}
function current(){const study=data.studies[si];return [study,study.clips[ci]];}
function readForm(form){return Object.fromEntries(new FormData(form).entries());}
function fill(form,values){[...form.elements].forEach(e=>{if(e.name)e.value=(values||{})[e.name]||'';e.disabled=locked;});}
async function save(){if(locked)return;const [s,c]=current();state.annotations.clips[c.clip_audit_id]=readForm(document.querySelector('#clip-form'));state.annotations.studies[s.audit_id]=readForm(document.querySelector('#study-form'));document.querySelector('#save-state').textContent='Saving';const r=await fetch('/api/checkpoint',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)});if(!r.ok)throw new Error('autosave failed');document.querySelector('#save-state').textContent='Saved';}
function render(){const [s,c]=current();document.querySelector('#study-id').textContent=s.audit_id;document.querySelector('#study-counter').textContent=`Study ${si+1} of ${data.studies.length}`;document.querySelector('#clip-counter').textContent=`Clip ${ci+1} of ${s.clips.length}`;document.querySelector('#tier').textContent=c.evidence_tier.replaceAll('_',' ');document.body.classList.toggle('source-only',c.source_only);document.querySelector('#model-caption').textContent=c.evidence_tier==='EXACT_MODEL_INPUT'?'Exact model input':'Verified equivalent replay';const src=document.querySelector('#source-image');src.src=c.source_media_id?`/media/${encodeURIComponent(c.source_media_id)}`:'';const model=document.querySelector('#model-image');model.src=c.model_input_media_id?`/media/${encodeURIComponent(c.model_input_media_id)}`:'';fill(document.querySelector('#clip-form'),state.annotations.clips[c.clip_audit_id]);fill(document.querySelector('#study-form'),state.annotations.studies[s.audit_id]);document.querySelector('#lock-review').disabled=locked;}
async function move(ds,dc){await save();si=Math.max(0,Math.min(data.studies.length-1,si+ds));if(ds)ci=0;const s=data.studies[si];ci=Math.max(0,Math.min(s.clips.length-1,ci+dc));render();}
async function lockReview(){await save();if(!confirm('Lock this reader record? It cannot be edited afterward.'))return;const r=await fetch('/api/lock',{method:'POST'});if(!r.ok)throw new Error('lock failed');locked=true;render();document.querySelector('#save-state').textContent='Locked';}
async function init(){fields(document.querySelector('#presence-fields'),presence);fields(document.querySelector('#study-fields'),studyFields);data=await (await fetch('/api/manifest')).json();state=await (await fetch('/api/checkpoint')).json();state.annotations=state.annotations||{studies:{},clips:{}};state.annotations.studies=state.annotations.studies||{};state.annotations.clips=state.annotations.clips||{};locked=!!state.locked;render();document.querySelectorAll('select').forEach(e=>e.addEventListener('change',()=>save().catch(console.error)));document.querySelectorAll('input').forEach(e=>e.addEventListener('change',()=>save().catch(console.error)));}
document.querySelector('#previous-study').onclick=()=>move(-1,0);document.querySelector('#next-study').onclick=()=>move(1,0);document.querySelector('#previous-clip').onclick=()=>move(0,-1);document.querySelector('#next-clip').onclick=()=>move(0,1);document.querySelector('#lock-review').onclick=()=>lockReview().catch(console.error);document.addEventListener('contextmenu',e=>{if(e.target.tagName==='IMG')e.preventDefault();});document.addEventListener('keydown',e=>{if(['SELECT','INPUT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')move(0,-1);if(e.key==='ArrowRight')move(0,1);if(e.key==='ArrowUp')move(-1,0);if(e.key==='ArrowDown')move(1,0);});init().catch(e=>{document.querySelector('#save-state').textContent='Interface error';console.error(e);});
"""


def build_blinded_interface_package(
    *,
    technical_manifest_csv: Path,
    reader_manifest_csv: Path,
    second_reader_manifest_csv: Path,
    media_root: Path,
    output_root: Path,
) -> InterfacePackageResult:
    output_root = require_restricted_destination(output_root)
    media_root = require_restricted_destination(media_root)
    if output_root.exists():
        raise FileExistsError("refusing to overwrite the audit interface")
    technical = pd.read_csv(technical_manifest_csv).fillna("")
    readers = pd.read_csv(reader_manifest_csv)
    second = pd.read_csv(second_reader_manifest_csv)
    require_columns(
        technical,
        [
            "audit_id",
            "clip_audit_id",
            "evidence_tier",
            "source_media_id",
            "model_input_media_id",
            "model_input_verified",
            "source_only",
        ],
        "technical interface manifest",
    )
    require_columns(readers, ["audit_id", "review_order"], "primary reader manifest")
    require_columns(second, ["audit_id", "review_order"], "second reader manifest")
    if technical[["audit_id", "clip_audit_id"]].astype(str).duplicated().any():
        raise ValueError("technical interface manifest contains duplicate clips")
    reader_ids = set(readers["audit_id"].astype(str))
    technical_ids = set(technical["audit_id"].astype(str))
    if reader_ids != technical_ids:
        raise ValueError("technical interface manifest differs from the locked primary roster")
    if not set(second["audit_id"].astype(str)).issubset(reader_ids):
        raise ValueError("second-reader assignment left the primary roster")
    visible_rows = [reader_visible_record(row) for row in technical.to_dict(orient="records")]
    visible = pd.DataFrame(visible_rows)
    if PROHIBITED_READER_FIELDS.intersection(visible.columns):
        raise Tier1BlockedError(BLOCKED_UNSAFE_OUTPUT, "reader-visible manifest exposes a prohibited field")
    verified_tiers = visible["evidence_tier"].isin(
        {"EXACT_MODEL_INPUT", "VERIFIED_EQUIVALENT_REPLAY"}
    )
    if not visible["model_input_verified"].astype(bool).eq(verified_tiers).all():
        raise ValueError("model-input verification flag is inconsistent with evidence tier")
    if not visible["source_only"].astype(bool).eq(
        visible["evidence_tier"].eq("SOURCE_ACQUISITION_ONLY")
    ).all():
        raise ValueError("source-only flag is inconsistent with evidence tier")
    if visible.loc[verified_tiers, "model_input_media_id"].astype(str).eq("").any():
        raise ValueError("verified model-input tier lacks model-input media")
    if visible.loc[visible["source_only"].astype(bool), "source_media_id"].astype(str).eq("").any():
        raise ValueError("source-only tier lacks source media")
    if visible.loc[~verified_tiers, "model_input_media_id"].astype(str).ne("").any():
        raise ValueError("unverified tier exposes model-input media")
    nonempty_media = pd.concat(
        [visible["source_media_id"], visible["model_input_media_id"]], ignore_index=True
    ).astype(str)
    nonempty_media = nonempty_media[nonempty_media.ne("")]
    if nonempty_media.duplicated().any():
        raise ValueError("opaque media tokens must be unique")
    media_ids = set(
        value
        for column in ("source_media_id", "model_input_media_id")
        for value in visible[column].astype(str)
        if value
    )
    for media_id in media_ids:
        if not READER_ID_PATTERN.fullmatch(media_id):
            raise ValueError("media IDs must be opaque path-free tokens")
        if not (media_root / f"{media_id}.png").is_file():
            raise FileNotFoundError(f"missing restricted media token {media_id}")

    order = dict(zip(readers["audit_id"].astype(str), readers["review_order"]))
    studies: list[dict[str, Any]] = []
    for audit_id, group in visible.groupby("audit_id", sort=False):
        studies.append(
            {
                "audit_id": str(audit_id),
                "review_order": int(order[str(audit_id)]),
                "clips": group.drop(columns=["audit_id", "review_order"]).to_dict(orient="records"),
            }
        )
    studies.sort(key=lambda row: (row["review_order"], row["audit_id"]))
    second_ids = set(second["audit_id"].astype(str))
    primary_payload = {"reader_role": "primary", "studies": studies}
    second_payload = {
        "reader_role": "second",
        "studies": [row for row in studies if row["audit_id"] in second_ids],
    }

    output_root.mkdir(parents=True, mode=0o700)
    os.chmod(output_root, 0o700)
    (output_root / "index.html").write_text(_interface_html(), encoding="utf-8")
    (output_root / "style.css").write_text(_interface_css(), encoding="utf-8")
    (output_root / "app.js").write_text(_interface_js(), encoding="utf-8")
    write_json(output_root / "primary_reader_manifest.json", primary_payload)
    write_json(output_root / "second_reader_manifest.json", second_payload)
    write_json(
        output_root / "interface_policy.json",
        {
            "status": AUDIT_INTERFACE_READY,
            "reader_visible_fields": list(READER_VISIBLE_FIELDS),
            "prohibited_reader_fields": sorted(PROHIBITED_READER_FIELDS),
            "ocr_available": False,
            "automated_content_annotation": False,
            "image_export_button": False,
            "unrestricted_image_export_route": False,
            "autosave": True,
            "checkpoint_resume": True,
            "annotation_lock": True,
            "second_reader_independent": True,
        },
    )
    summary = {
        "status": AUDIT_INTERFACE_READY,
        "primary_studies": len(primary_payload["studies"]),
        "second_reader_studies": len(second_payload["studies"]),
        "clips": int(len(visible)),
        "media_items": len(media_ids),
        "reader_blinding_validated": True,
        "autosave_resume_validated": True,
        "annotation_lock_available": True,
        "ocr_available": False,
        "automated_content_annotation": False,
        "image_export_button": False,
        "unrestricted_image_export_route": False,
        "technical_manifest_sha256": sha256_file(technical_manifest_csv),
    }
    write_json(output_root / "interface_summary.json", summary)
    return InterfacePackageResult(output_root, summary)
