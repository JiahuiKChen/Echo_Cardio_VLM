#!/usr/bin/env python3
"""Build the A1122 internal-review draft from authenticated aggregate results.

This is a presentation-only consumer. It does not fit models, recompute a
bootstrap, read patient rows, or authorize conference/public submission. Both
expected SHA-256 values must come from the independently observed aggregate
transport record. Without those real inputs this command produces no PDF.

Example (substitute the observed hashes, never guessed values)::

  python build_asa_draft.py --bundle aggregate_bundle.json \
    --supplement asa_supplement.json --bundle-sha256 <observed-sha> \
    --supplement-sha256 <observed-sha> --validate-only

Omit --validate-only to render output/pdf/asa_a1122_revalidation_draft.pdf.
After rendering, inspect a PNG preview at the intended display aspect ratio.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from xml.sax.saxutils import escape


REPOSITORY = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY / "scripts"))
import render_lvef_revalidation_results as renderer
from lvef_revalidation_inference import CORE_CLAIMS, core_holm


TITLE = ("Multimodal Echocardiography Modeling in MIMIC-IV-ECHO: Fusion of Cine "
         "Embeddings and Structured Measurements for Ejection Fraction and Multitask Prediction")
LABELS = {
    "arch_diam": "Aortic arch diameter",
    "ascending_aorta_diameter": "Ascending aortic diameter",
    "av_pk_vel": "Aortic valve peak velocity",
    "inf_lat_thickness": "Inferolateral wall thickness",
    "ivc_diam": "IVC diameter",
    "la_4ch_length": "LA four-chamber length",
    "la_dimen": "LA anteroposterior diameter",
    "lat_e_prime": "Lateral e-prime velocity",
    "left_ventricular_end_diastolic_diameter": "LV end-diastolic diameter",
    "left_ventricular_end_systolic_diameter": "LV end-systolic diameter",
    "lvot_diam": "LVOT diameter",
    "lvot_vti": "LVOT velocity-time integral",
    "mv_peak_a": "Mitral A-wave peak velocity",
    "mv_peak_e": "Mitral E-wave peak velocity",
    "ra_length": "RA length",
    "rv_diam": "RV diameter",
    "sept_e_prime": "Septal e-prime velocity",
    "septal_thickness": "Interventricular septal thickness",
    "sinus_diam": "Aortic sinus diameter",
    "tricuspid_annular_plane_systolic_excursion": "TAPSE",
    "tricuspid_regurgitant_peak_velocity": "TR peak velocity",
}
VELOCITIES = {"av_pk_vel", "lat_e_prime", "sept_e_prime", "mv_peak_a", "mv_peak_e",
              "tricuspid_regurgitant_peak_velocity"}
CONTRASTS = ("early_fusion_minus_vision_only", "early_fusion_minus_structured_only",
             "structured_only_minus_vision_only")
MODALITY_LABELS = ("Vision only", "Structured only", "Early fusion")
COLORS = ("#284B70", "#287C79", "#B95C39")
INK, MUTED, PALE, LINE = "#163249", "#536879", "#F0F5F7", "#DCE5EA"
WIDTH, HEIGHT = 1600, 900
SHA = re.compile(r"[0-9a-f]{64}\Z")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "ASA_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def load_bound(path: Path, expected: str) -> tuple[dict[str, Any], bytes]:
    require(SHA.fullmatch(expected) is not None and path.is_file() and not path.is_symlink(), "ASA_INPUT_AUTHORITY_REQUIRED")
    require(0 < path.stat().st_size <= 32 * 1024 * 1024, "ASA_AGGREGATE_SIZE_INVALID")
    body = path.read_bytes()
    require(hashlib.sha256(body).hexdigest() == expected, "ASA_INPUT_HASH_MISMATCH")
    value = json.loads(body, object_pairs_hook=unique_pairs)
    require(isinstance(value, dict) and canonical(value) == body, "ASA_NONCANONICAL_AGGREGATE")
    return value, body


def close(left, right) -> bool:
    return type(left) in (int, float) and type(right) in (int, float) and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)


def paired(value: Mapping[str, Any]) -> None:
    require(set(value) == {"effect", "interval", "p_value", "valid_replicates", "undefined_replicates",
            "undefined_frequency", "replicates", "interval_method", "p_value_method"}, "ASA_PAIRED_SCHEMA_INVALID")
    renderer._interval(value)
    require(value["interval_method"] == "percentile_95"
            and value["p_value_method"] == "null_centered_two_sided_plus_one"
            and close(value["undefined_frequency"], value["undefined_replicates"] / 10000), "ASA_INFERENCE_METHOD_INVALID")


def validated_inputs(bundle_path: Path, supplement_path: Path, *, bundle_sha256: str,
                     supplement_sha256: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    bundle, bundle_body = load_bound(bundle_path, bundle_sha256)
    supplement, _ = load_bound(supplement_path, supplement_sha256)
    candidate = renderer.validate_bundle(bundle)
    require(len(candidate["strict_panel"]) == 21 and set(candidate["strict_panel"]) == set(LABELS), "ASA_EXACT_PANEL_REQUIRED")
    expected = {"schema_version", "artifact_type", "status", "strict_panel", "source_sha256", "conditions",
                "secondary_intervals_complete_in_bound_paired_report", "model_refitted", "bootstrap_recomputed",
                "patient_level_outputs", "poster_export_authorized"}
    require(set(supplement) == expected and supplement["schema_version"] == 1
            and supplement["artifact_type"] == "lvef_revalidation_asa_aggregate_supplement_v1"
            and supplement["status"] == "PASS_VALIDATED_ASA_AGGREGATE_SUPPLEMENT"
            and supplement["strict_panel"] == candidate["strict_panel"]
            and supplement["secondary_intervals_complete_in_bound_paired_report"] is True
            and all(supplement[k] is False for k in ("model_refitted", "bootstrap_recomputed", "patient_level_outputs", "poster_export_authorized")),
            "ASA_VALIDATED_SUPPLEMENT_REQUIRED")
    bindings = supplement["source_sha256"]
    require(set(bindings) == {"bundle", "paired_report", "aggregate_safety", "evaluation_receipt", "spec", "frozen_models"}
            and all(isinstance(v, str) and SHA.fullmatch(v) for v in bindings.values())
            and bindings["bundle"] == hashlib.sha256(bundle_body).hexdigest()
            and bindings["paired_report"] == candidate["report_sha256"]
            and bindings["aggregate_safety"] == bundle["aggregate_safety"]["safety_receipt_sha256"]
            and bindings["spec"] == candidate["spec_sha256"]
            and bindings["frozen_models"] == candidate["frozen_sha256"], "ASA_SUPPLEMENT_SOURCE_MISMATCH")
    require(set(supplement["conditions"]) == set(renderer.CONDITIONS), "ASA_COMPLETE_CONDITIONS_REQUIRED")
    renderer._no_rows(supplement)
    for condition in renderer.CONDITIONS:
        value = supplement["conditions"][condition]
        require(set(value) == {"lvef_mae", "strict_panel_macro", "core_multiplicity", "secondary_logistic_lt40",
                "secondary_binary_holm", "panel_summary"}
                and value["panel_summary"] == candidate["panel_summary"][condition], "ASA_CONDITION_BINDING_INVALID")
        anchor, macro = value["lvef_mae"], value["strict_panel_macro"]
        require(set(anchor) == {"contrasts", "one_class_replicates", "one_class_frequency", "observed", "replicates", "shared_subject_multiplicities"}
                and anchor["replicates"] == 10000 and anchor["shared_subject_multiplicities"] is True,
                "ASA_ANCHOR_BOOTSTRAP_INVALID")
        require(set(macro) == {"macro_normalized_mae", "macro_contrasts", "locked_task_count", "roster_subjects",
                "shared_subject_multiplicities", "undefined_task_invalidates_macro"}
                and macro["locked_task_count"] == 21
                and macro["roster_subjects"] == candidate["flow"]["imaging_split_counts"]["test"]
                and macro["shared_subject_multiplicities"] is True and macro["undefined_task_invalidates_macro"] is True,
                "ASA_MACRO_BOOTSTRAP_INVALID")
        require(set(anchor["observed"]) == set(macro["macro_normalized_mae"]) == set(renderer.MODALITIES)
                and set(anchor["contrasts"]) == set(macro["macro_contrasts"]) == set(CONTRASTS), "ASA_MODALITY_SET_INVALID")
        for modality in renderer.MODALITIES:
            rows = [r for r in candidate["rows"] if r["condition"] == condition and r["modality"] == modality]
            require(close(anchor["observed"][modality], next(r for r in rows if r["target"] == "lvef")["metrics"]["mae"])
                    and close(macro["macro_normalized_mae"][modality], sum(r["metrics"]["normalized_mae"] for r in rows if r["target"] != "lvef") / 21),
                    "ASA_ESTIMATE_BINDING_MISMATCH")
            for row in rows:
                target = row["target"]
                expected_unit = "EF_percentage_points" if target == "lvef" else "cm/s" if target in VELOCITIES else "mm"
                require(row["unit"] == expected_unit and all(type(n) is int and n > 0 for n in row["denominators"].values()), "ASA_TASK_UNIT_OR_SUPPORT_MISMATCH")
        for contrast in CONTRASTS:
            left, right = contrast.split("_minus_")
            for values, effects in ((anchor["observed"], anchor["contrasts"]), (macro["macro_normalized_mae"], macro["macro_contrasts"])):
                paired(effects[contrast])
                require(close(effects[contrast]["effect"], values[left] - values[right]), "ASA_EFFECT_BINDING_MISMATCH")
        if condition == "primary":
            p_values = {CORE_CLAIMS[i]: anchor["contrasts"][key]["p_value"] for i, key in enumerate(CONTRASTS[:2])}
            p_values.update({CORE_CLAIMS[i + 2]: macro["macro_contrasts"][key]["p_value"] for i, key in enumerate(CONTRASTS[:2])})
            expected_core = core_holm(p_values, strict_panel=candidate["strict_panel"], strict_panel_locked=True)
            require(value["core_multiplicity"] == expected_core
                    and candidate["core_holm"] == expected_core["adjusted_p_values"], "ASA_FOUR_CLAIM_HOLM_MISMATCH")
        else:
            require(value["core_multiplicity"] == {"status": "SECONDARY_NO_CORE_CLAIM"}, "ASA_SENSITIVITY_CORE_INVALID")
    return candidate, supplement


def fonts(font_directory: Path | None) -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if font_directory is None:
        roots = [Path("/usr/share/fonts/truetype/dejavu"),
                 Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/Resources/fonts/truetype"]
        font_directory = next((p for p in roots if (p / "DejaVuSans.ttf").is_file()), None)
    require(font_directory is not None, "ASA_FONT_DIRECTORY_REQUIRED")
    for name, filename in (("ASA", "DejaVuSans.ttf"), ("ASA-Bold", "DejaVuSans-Bold.ttf")):
        require((font_directory / filename).is_file(), "ASA_FONT_MISSING")
        pdfmetrics.registerFont(TTFont(name, str(font_directory / filename)))


def render(candidate, supplement, *, output: Path, font_directory: Path | None,
           bundle_sha256: str, supplement_sha256: str) -> dict[str, Any]:
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from reportlab.pdfbase.pdfmetrics import stringWidth
    fonts(font_directory)
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(WIDTH, HEIGHT), pageCompression=1, invariant=1)
    canvas.setTitle(TITLE)
    canvas.setAuthor("Author and affiliation details not supplied; internal review draft")
    canvas.setSubject("A1122; aggregate-only revalidation; draft, not authorized for submission")

    def rect(x, y, w, h, fill):
        canvas.setFillColor(HexColor(fill)); canvas.rect(x, y, w, h, fill=1, stroke=0)

    def text(x, y, value, size=18, color=INK, bold=False, right=False):
        canvas.setFillColor(HexColor(color)); canvas.setFont("ASA-Bold" if bold else "ASA", size)
        (canvas.drawRightString if right else canvas.drawString)(x, y, str(value))

    def paragraph(value, x, top, width, *, size=17, leading=23, color=INK, bold=False, max_height=None):
        style = ParagraphStyle("asa", fontName="ASA-Bold" if bold else "ASA", fontSize=size, leading=leading,
                               textColor=HexColor(color))
        p = Paragraph(value, style)
        _, height = p.wrap(width, HEIGHT)
        require(max_height is None or height <= max_height, "ASA_LAYOUT_TEXT_OVERFLOW")
        p.drawOn(canvas, x, top - height)
        return height

    def number(value, digits=2):
        return "Not estimable" if value is None else f"{value:.{digits}f}"

    def probability(value):
        return "<0.0001" if value < 0.0001 else f"{value:.4f}"

    def effect(value, digits):
        interval = value["interval"]
        if interval is None:
            return f"{number(value['effect'], digits)} [CI undefined]"
        return f"{value['effect']:+.{digits}f} [{interval[0]:+.{digits}f}, {interval[1]:+.{digits}f}]"

    rect(0, 0, WIDTH, HEIGHT, "#FFFFFF")
    rect(0, 744, WIDTH, 156, INK)
    text(40, 873, "ANESTHESIOLOGY 2026  |  A1122", 17, "#BBD8E5", True)
    text(1560, 873, "DRAFT - INTERNAL REVIEW - NOT FOR SUBMISSION", 15, "#F1C69B", True, right=True)
    paragraph(escape(TITLE), 40, 850, 1515, size=31, leading=38, color="#FFFFFF", bold=True, max_height=79)
    text(40, 758, "Author and affiliation details awaiting confirmation", 16, "#D0DFE6")

    flow = candidate["flow"]
    split = flow["lvef_common_counts"]
    rect(40, 668, 1520, 58, PALE)
    text(58, 703, f"{flow['counts']['selected_subjects']:,} selected subjects  |  {flow['counts']['imaging_eligible']:,} with cine features  |  21 measurement targets + LVEF", 21, bold=True)
    text(58, 680, f"LVEF common rows: {split['train']:,} training / {split['val']:,} validation / {split['test']:,} test. Identical rows and labels across modalities for each target.", 17)

    primary = supplement["conditions"]["primary"]
    anchor, macro = primary["lvef_mae"], primary["strict_panel_macro"]
    x, w = 40, 650

    def result_card(top, title, subtitle, values, effects, *, claim_start, digits, mean_digits):
        text(x, top, title, 25, bold=True)
        text(x, top - 27, subtitle, 16, MUTED)
        for index, modality in enumerate(renderer.MODALITIES):
            column = x + index * 221
            rect(column, top - 99, 207, 55, PALE)
            text(column + 12, top - 64, MODALITY_LABELS[index], 15, COLORS[index], True)
            text(column + 12, top - 90, number(values[modality], mean_digits), 25, COLORS[index], True)
        text(x, top - 125, "Paired difference [95% CI]", 17, bold=True)
        text(x + w, top - 125, "Holm p", 17, bold=True, right=True)
        for index, key in enumerate(CONTRASTS[:2]):
            y = top - 154 - index * 35
            label = "Fusion - vision" if index == 0 else "Fusion - structured"
            text(x, y, label, 18)
            text(x + 232, y, effect(effects[key], digits), 18)
            text(x + w, y, probability(candidate["core_holm"][CORE_CLAIMS[claim_start + index]]), 18, right=True)
        undefined = max(effects[key]["undefined_replicates"] for key in CONTRASTS[:2])
        text(x, top - 219, f"10,000 paired subject draws; undefined draws: {undefined:,}.", 15, MUTED)

    result_card(636, "LVEF prediction", "Mean absolute error in EF percentage points; lower is better.",
                anchor["observed"], anchor["contrasts"], claim_start=0, digits=2, mean_digits=2)
    result_card(377, "Strict-panel summary", "Mean of 21 task MAEs divided by each training IQR.",
                macro["macro_normalized_mae"], macro["macro_contrasts"], claim_start=2, digits=3, mean_digits=3)

    rx, rw = 730, 830
    text(rx, 636, "Complete 21-target measurement panel", 25, bold=True)
    text(rx, 608, "Primary-condition MAE in displayed physical units; observed labels only.", 16, MUTED)
    rect(rx, 573, rw, 26, INK)
    positions = {"target": rx + 9, "unit": rx + 389, "n": rx + 466, "v": rx + 579, "s": rx + 697, "f": rx + 815}
    text(positions["target"], 581, "Measurement", 15, "#FFFFFF", True)
    text(positions["unit"], 581, "Unit", 15, "#FFFFFF", True, right=True)
    for key, title in (("n", "Test n"), ("v", "Vision"), ("s", "Structured"), ("f", "Fusion")):
        text(positions[key], 581, title, 15, "#FFFFFF", True, right=True)
    for index, target in enumerate(candidate["strict_panel"]):
        y = 552 - index * 19.5
        if index % 2 == 0:
            rect(rx, y - 5, rw, 19.5, "#F3F6F8")
        rows = [r for r in candidate["rows"] if r["condition"] == "primary" and r["target"] == target]
        require(stringWidth(LABELS[target], "ASA", 16) < 350, "ASA_TABLE_LABEL_OVERFLOW")
        text(positions["target"], y, LABELS[target], 16)
        text(positions["unit"], y, rows[0]["unit"], 15, MUTED, right=True)
        text(positions["n"], y, rows[0]["denominators"]["test"], 16, right=True)
        for key, modality, color in zip(("v", "s", "f"), renderer.MODALITIES, COLORS):
            row = next(r for r in rows if r["modality"] == modality)
            text(positions[key], y, number(row["metrics"]["mae"]), 17, color, right=True)
    text(rx, 140, "No task removed for unfavorable performance. Native-unit MAE is unnormalized.", 15, MUTED)

    rect(40, 124, 1520, 1, LINE)
    paragraph("<b>METHODS</b>  Frozen EchoPrime cine features, deterministic study mean pooling and supervised Ridge models. "
              "Target/alias/formula/family masks precede training-only transforms; validation selects the fixed model. "
              "Four primary fusion contrasts share one Holm family. Negative differences favor fusion; intervals are conditional on fixed models.",
              40, 112, 744, size=14.5, leading=18.5, max_height=93)
    paragraph("<b>LIMITATIONS</b>  Existing split had historical test exposure; this is not external validation. "
              "Operational measurement conventions remain source-unverified; LVEF method and native-unit declaration are unspecified. "
              "Mitral E in ms is excluded, never converted or merged. Naturally missing labels and residual in-sector annotations remain unvalidated.",
              814, 112, 746, size=14.5, leading=18.5, max_height=93)
    text(40, 12, f"Aggregate bundle {bundle_sha256[:16]}  |  Paired supplement {supplement_sha256[:16]}  |  All six prespecified conditions retained in the bound aggregate record; primary shown here.", 10, MUTED)
    canvas.showPage(); canvas.save()
    body = buffer.getvalue()
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(body))
    require(len(reader.pages) == 1 and tuple(float(v) for v in reader.pages[0].mediabox) == (0., 0., float(WIDTH), float(HEIGHT)), "ASA_SINGLE_16_9_PAGE_REQUIRED")
    extracted = reader.pages[0].extract_text()
    require(all(label in extracted for label in LABELS.values()) and "A1122" in extracted
            and "NOT FOR SUBMISSION" in extracted, "ASA_COMPLETE_TEXT_REQUIRED")
    require(not output.exists() and not output.is_symlink(), "ASA_OUTPUT_ALREADY_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644), "wb") as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    return {"status": "PASS_ASA_DRAFT_RENDERED_VISUAL_REVIEW_REQUIRED", "pages": 1, "aspect_ratio": "16:9",
            "strict_panel_targets": 21, "anchor_targets": 1, "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body), "bundle_sha256": bundle_sha256, "supplement_sha256": supplement_sha256,
            "poster_export_authorized": False, "patient_level_data_read": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--supplement-sha256", required=True)
    parser.add_argument("--output", type=Path, default=REPOSITORY / "output/pdf/asa_a1122_revalidation_draft.pdf")
    parser.add_argument("--font-directory", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    candidate, supplement = validated_inputs(args.bundle, args.supplement,
        bundle_sha256=args.bundle_sha256, supplement_sha256=args.supplement_sha256)
    if args.validate_only:
        result = {"status": "PASS_ASA_AGGREGATE_INPUTS", "strict_panel_targets": 21,
                  "anchor_targets": 1, "pdf_created": False, "poster_export_authorized": False}
    else:
        result = render(candidate, supplement, output=args.output, font_directory=args.font_directory,
                        bundle_sha256=args.bundle_sha256, supplement_sha256=args.supplement_sha256)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
