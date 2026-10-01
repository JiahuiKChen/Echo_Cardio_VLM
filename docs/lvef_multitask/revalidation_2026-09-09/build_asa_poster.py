#!/usr/bin/env python3
"""Render the audience-facing A1122 poster from hash-bound aggregate inputs.

This presentation-only successor preserves build_asa_draft.py and delegates all
scientific aggregate validation to it. Author metadata is independently bound.
No models, resamples or intervals are recomputed. Output stays outside Git;
removing draft copy is not conference submission or export authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

import build_asa_draft as base


MODALITIES = base.renderer.MODALITIES
NAMES = ("Video only", "Recorded measurements", "Combined")
SHORT_NAMES = ("Video", "Recorded", "Combined")
INK, MUTED, PALE, LINE = base.INK, base.MUTED, base.PALE, base.LINE
WIDTH, HEIGHT = base.WIDTH, base.HEIGHT
DISPLAY_GROUPS = (
    ("Left-heart<br/>size and<br/>walls", (
        "left_ventricular_end_diastolic_diameter", "left_ventricular_end_systolic_diameter",
        "septal_thickness", "inf_lat_thickness", "la_dimen", "la_4ch_length")),
    ("Mitral and<br/>tissue<br/>Doppler", (
        "mv_peak_e", "mv_peak_a", "sept_e_prime", "lat_e_prime")),
    ("Aorta and<br/>LV outflow", (
        "sinus_diam", "ascending_aorta_diameter", "arch_diam", "lvot_diam", "lvot_vti", "av_pk_vel")),
    ("Right heart", (
        "ra_length", "rv_diam", "tricuspid_annular_plane_systolic_excursion",
        "tricuspid_regurgitant_peak_velocity", "ivc_diam")),
)


def read_authors(path: Path, expected: str) -> dict:
    base.require(path.is_file() and not path.is_symlink()
                 and base.SHA.fullmatch(expected) is not None, "POSTER_AUTHOR_SOURCE_REQUIRED")
    body = path.read_bytes()
    base.require(len(body) < 32768 and hashlib.sha256(body).hexdigest() == expected,
                 "POSTER_AUTHOR_HASH_MISMATCH")
    value = json.loads(body, object_pairs_hook=base.unique_pairs)
    authors, affiliations = value.get("authors"), value.get("affiliations")
    base.require(isinstance(authors, list) and len(authors) == 6
                 and isinstance(affiliations, list) and len(affiliations) == 6,
                 "POSTER_SIX_AUTHORS_REQUIRED")
    for author in authors:
        base.require(set(author) == {"name", "credentials", "affiliation"}
                     and all(isinstance(author[k], str) and 0 < len(author[k]) < 120
                             for k in ("name", "credentials"))
                     and type(author["affiliation"]) is int
                     and 1 <= author["affiliation"] <= 6, "POSTER_AUTHOR_FIELDS_INVALID")
    base.require(all(isinstance(a, str) and 0 < len(a) < 240 for a in affiliations),
                 "POSTER_AFFILIATIONS_INVALID")
    return value


def display_measurement(row):
    """LVOT VTI alone is displayed in cm; validated source values remain in mm."""
    if row["target"] == "lvot_vti":
        base.require(row["unit"] == "mm", "POSTER_VTI_SOURCE_UNIT_INVALID")
        return "cm", row["metrics"]["mae"] / 10.0
    return row["unit"], row["metrics"]["mae"]


def read_logo(path: Path, expected: str) -> dict:
    """Read the original supplied PNG; use alpha bounds only for PDF placement."""
    from PIL import Image
    base.require(path.is_file() and not path.is_symlink()
                 and base.SHA.fullmatch(expected) is not None, "POSTER_LOGO_SOURCE_REQUIRED")
    body = path.read_bytes()
    base.require(len(body) < 16 * 1024 * 1024 and hashlib.sha256(body).hexdigest() == expected,
                 "POSTER_LOGO_HASH_MISMATCH")
    with Image.open(io.BytesIO(body)) as logo:
        base.require(logo.format == "PNG" and logo.mode in ("RGB", "RGBA"), "POSTER_LOGO_PNG_REQUIRED")
        bounds = logo.getchannel("A").getbbox() if logo.mode == "RGBA" else (0, 0, *logo.size)
        base.require(bounds is not None, "POSTER_LOGO_EMPTY")
        return {"body": body, "sha256": expected, "size": logo.size, "visible_bounds": bounds}


def render(candidate, supplement, authors, *, output, font_directory,
           bundle_sha256, supplement_sha256, authors_sha256, logos):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from reportlab.pdfbase.pdfmetrics import registerFontFamily, stringWidth
    from reportlab.lib.utils import ImageReader
    from pypdf import PdfReader

    base.fonts(font_directory)
    registerFontFamily("ASA", normal="ASA", bold="ASA-Bold", italic="ASA", boldItalic="ASA-Bold")
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(WIDTH, HEIGHT), pageCompression=1, invariant=1)
    canvas.setTitle(base.TITLE)
    canvas.setAuthor("; ".join(a["name"] for a in authors["authors"]))
    canvas.setSubject("ANESTHESIOLOGY 2026 | A1122 | LVEF and measurement prediction")

    def rect(x, y, w, h, fill):
        canvas.setFillColor(HexColor(fill)); canvas.rect(x, y, w, h, fill=1, stroke=0)

    def text(x, y, value, size=17, color=INK, bold=False, right=False):
        canvas.setFillColor(HexColor(color)); canvas.setFont("ASA-Bold" if bold else "ASA", size)
        (canvas.drawRightString if right else canvas.drawString)(x, y, str(value))

    def paragraph(value, x, top, width, *, size=17, leading=22, color=INK,
                  bold=False, max_height=None):
        style = ParagraphStyle("poster", fontName="ASA-Bold" if bold else "ASA",
                               fontSize=size, leading=leading, textColor=HexColor(color))
        p = Paragraph(value, style)
        _, h = p.wrap(width, HEIGHT)
        base.require(max_height is None or h <= max_height, "POSTER_TEXT_OVERFLOW")
        p.drawOn(canvas, x, top - h)
        return h

    def line(x1, y1, x2, y2, color=LINE, width=.8):
        canvas.setStrokeColor(HexColor(color)); canvas.setLineWidth(width)
        canvas.line(x1, y1, x2, y2)

    def contrast(value, digits):
        lo, hi = value["interval"]
        return f"{value['effect']:+.{digits}f} [{lo:+.{digits}f}, {hi:+.{digits}f}]"

    primary = supplement["conditions"]["primary"]
    display_panel = [target for _, targets in DISPLAY_GROUPS for target in targets]
    base.require(len(display_panel) == len(set(display_panel)) == len(candidate["strict_panel"])
                 and set(display_panel) == set(candidate["strict_panel"]), "POSTER_DISPLAY_PANEL_MISMATCH")
    anchor, macro = primary["lvef_mae"], primary["strict_panel_macro"]
    flow = candidate["flow"]
    split = flow["lvef_common_counts"]
    rows = [r for r in candidate["rows"] if r["condition"] == "primary"]
    projection = {"lvef": [], "measurements": [], "paired_comparisons": [],
                  "panel_scores": macro["macro_normalized_mae"]}

    rect(0, 0, WIDTH, HEIGHT, "#FFFFFF")
    rect(0, 727, WIDTH, 173, INK)
    text(40, 880, "ANESTHESIOLOGY 2026  |  A1122", 13, "#BBD8E5", True)
    logo_placements = {}
    for name, right in (("bmc", 1412), ("bu", 1560)):
        logo = logos[name]
        left, top, edge, bottom = logo["visible_bounds"]
        width, height = logo["size"]
        scale = 56 / (bottom - top)
        visible_x, visible_y = right - (edge - left) * scale, 736
        canvas.drawImage(ImageReader(io.BytesIO(logo["body"])),
                         visible_x - left * scale, visible_y - (height - bottom) * scale,
                         width=width * scale, height=height * scale, mask="auto")
        logo_placements[name] = {"source_sha256": logo["sha256"],
            "original_pixels_preserved": True, "aspect_ratio_preserved": True,
            "visible_bounds_points": [visible_x, visible_y, right, 792]}
    paragraph(escape(base.TITLE), 40, 863, 1520, size=28, leading=32,
              color="#FFFFFF", bold=True, max_height=65)
    author_line = ";  ".join(escape(a["name"] + ", " + a["credentials"])
                              + f"<super>{a['affiliation']}</super>" for a in authors["authors"])
    paragraph(author_line, 40, 791, 1210, size=17, leading=23, color="#FFFFFF", max_height=24)
    for start, top in ((0, 765), (3, 748)):
        value = "   ".join(f"<super>{i+1}</super> " + escape(authors["affiliations"][i])
                           for i in range(start, start + 3))
        paragraph(value, 40, top, 1210, size=11.4, leading=15.5,
                  color="#D0DFE6", max_height=16)

    rect(40, 663, 1520, 49, PALE)
    text(55, 690, f"{flow['counts']['imaging_eligible']:,} patients with echo video data  |  LVEF test: {split['test']:,} patients  |  21 additional measurements", 20, bold=True)
    text(55, 671, f"LVEF analysis: {split['train']:,} training / {split['val']:,} validation / {split['test']:,} test patients.", 14.5, MUTED)

    lx, lw, rx, rw = 40, 654, 735, 825
    text(lx, 638, "Question and design", 24, bold=True)
    paragraph("Does combining echo video information with other recorded measurements improve prediction of reported LVEF and other echo measurements?",
              lx, 620, lw, size=18, leading=23, max_height=70)
    steps = [
        "<b>Summarize videos.</b> A fixed EchoPrime encoder [1] converts clips to numerical summaries; average these within each examination.",
        "<b>Compare inputs.</b> Fit ridge regression per target from video, recorded measurements, or both (fusion). Remove the target and known shortcuts.",
        "<b>Evaluate fairly.</b> Choose settings on validation patients; compare all three models on the same test patients and reported values.",
    ]
    y = 539
    for index, value in enumerate(steps, 1):
        text(lx, y - 17, str(index), 19, base.COLORS[0], True)
        h = paragraph(value, lx + 27, y, lw - 27, size=15.5, leading=20.5, max_height=62)
        y -= h + 9
    base.require(y >= 372, "POSTER_WORKFLOW_OVERFLOW")

    text(lx, 362, "LVEF: average prediction error", 23, bold=True)
    text(lx, 339, "MAE: average error in EF percentage points. Bars: 95% CI; lower is better.", 14.3, MUTED)
    plot_x, plot_w, axis_y = lx + 233, 340, 226
    xmax = 10
    for tick in (0, 2, 4, 6, 8, 10):
        px = plot_x + plot_w * tick / xmax
        line(px, axis_y, px, 322, LINE, .6)
        text(px - 4, axis_y - 15, tick, 11.5, MUTED)
    line(plot_x, axis_y, plot_x + plot_w, axis_y, MUTED, .7)
    for index, modality in enumerate(MODALITIES):
        row = next(r for r in rows if r["target"] == "lvef" and r["modality"] == modality)
        value = row["metrics"]["mae"]
        interval = row["metric_intervals"]["mae"]["interval"]
        base.require(interval is not None, "POSTER_LVEF_INTERVAL_REQUIRED")
        cy = 307 - index * 32
        col = base.COLORS[index]
        text(lx, cy - 5, NAMES[index], 16, col, True)
        a, b, middle = (plot_x + plot_w * v / xmax for v in (*interval, value))
        line(a, cy, b, cy, col, 2.4)
        for endpoint in (a, b): line(endpoint, cy - 5, endpoint, cy + 5, col, 1.2)
        canvas.setFillColor(HexColor(col)); canvas.circle(middle, cy, 4.5, fill=1, stroke=0)
        text(lx + lw, cy - 5, f"{value:.2f}", 20, col, True, right=True)
        projection["lvef"].append({"modality": modality, "mae": value, "interval": interval,
                                    "test_n": row["denominators"]["test"]})

    def paired_table(x, width, top, values, claim_start, digits):
        text(x, top, "Difference in error: combined minus comparator (95% CI)", 12.2, MUTED, True)
        text(x + width, top, "Adjusted p", 12.2, MUTED, True, right=True)
        for i, key in enumerate(base.CONTRASTS[:2]):
            yy = top - 22 - i * 22
            label = "vs video" if i == 0 else "vs recorded"
            p = candidate["core_holm"][base.CORE_CLAIMS[claim_start + i]]
            text(x, yy, label, 15)
            text(x + 144, yy, contrast(values[key], digits), 15)
            text(x + width, yy, f"{p:.4f}", 15, right=True)
            projection["paired_comparisons"].append({"claim": base.CORE_CLAIMS[claim_start + i],
                "effect": values[key]["effect"], "interval": values[key]["interval"], "adjusted_p": p})
    paired_table(lx, lw, 197, anchor["contrasts"], 0, 2)

    text(rx, 638, "Prediction across 21 measurements", 24, bold=True)
    text(rx, 615, "Cells: MAE in the listed unit (scaled MAE). Lower is better for both.", 14.3, MUTED)
    text(rx, 598, "Scaled MAE = MAE / training IQR (middle-50% spread); unitless, not percentage error.", 12.6, MUTED)
    rect(rx, 569, rw, 23, INK)
    pos = {"group": rx + 8, "target": rx + 100, "unit": rx + 367, "n": rx + 422,
           "v": rx + 553, "s": rx + 686, "f": rx + 816}
    text(pos["group"], 576, "Group", 13.5, "#FFFFFF", True)
    text(pos["target"], 576, "Measurement", 14, "#FFFFFF", True)
    for key, name in (("unit", "Unit"), ("n", "Test n"), ("v", "Video"), ("s", "Recorded"), ("f", "Combined")):
        text(pos[key], 576, name, 13.5, "#FFFFFF", True, right=True)
    yy = 556
    for group_index, (group, targets) in enumerate(DISPLAY_GROUPS):
        group_height = len(targets) * 14.5
        rect(rx, yy + 11 - group_height, 92, group_height, "#E8F0F4")
        paragraph(group, pos["group"], yy + 9, 78, size=11.7, leading=14,
                  bold=True, max_height=group_height - 4)
        for i, target in enumerate(targets):
            if i % 2 == 0: rect(rx + 96, yy - 3.5, rw - 96, 14.5, "#F1F5F7")
            base.require(stringWidth(base.LABELS[target], "ASA", 13.3) < 224, "POSTER_LABEL_OVERFLOW")
            text(pos["target"], yy, base.LABELS[target], 13.3)
            target_rows = [r for r in rows if r["target"] == target]
            display_unit = display_measurement(target_rows[0])[0]
            text(pos["unit"], yy, display_unit, 12.5, MUTED, right=True)
            text(pos["n"], yy, target_rows[0]["denominators"]["test"], 13.3, right=True)
            record = {"target": target, "display_group": group.replace("<br/>", " "),
                      "display_unit": display_unit,
                      "test_n": target_rows[0]["denominators"]["test"],
                      "display_mae": {}, "scaled_mae": {}}
            for key, modality, color in zip(("v", "s", "f"), MODALITIES, base.COLORS):
                row = next(r for r in target_rows if r["modality"] == modality)
                _, value = display_measurement(row)
                scaled = row["metrics"]["normalized_mae"]
                cell = f"{value:.2f} ({scaled:.3f})"
                base.require(stringWidth(cell, "ASA", 13.3) < 118, "POSTER_VALUE_OVERFLOW")
                text(pos[key], yy, cell, 13.3, color, right=True)
                record["display_mae"][modality] = value
                record["scaled_mae"][modality] = scaled
            projection["measurements"].append(record)
            yy -= 14.5
        if group_index < len(DISPLAY_GROUPS) - 1:
            line(rx, yy + 9, rx + rw, yy + 9)
            yy -= 3
    base.require(yy >= 238, "POSTER_TABLE_OVERFLOW")

    text(rx, 231, "Overall scaled error across 21 measurements", 20, bold=True)
    for i, modality in enumerate(MODALITIES):
        text(rx + i * 278, 211, f"{SHORT_NAMES[i]}  {macro['macro_normalized_mae'][modality]:.3f}", 17, base.COLORS[i], True)
    text(rx, 195, "Equal-weight average of the 21 scaled MAEs shown above.", 11.8, MUTED)
    paired_table(rx, rw, 179, macro["macro_contrasts"], 2, 4)

    text(40, 117, "95% CIs: 10,000 paired patient resamples with fitted models fixed. P values: Holm-adjusted across four planned comparisons. Negative differences favor Combined.", 11.7, MUTED)
    line(40, 108, 1560, 108)
    text(40, 87, "What we found", 20, bold=True)
    paragraph("Combining video and recorded measurements did not demonstrate better LVEF prediction than video alone. It modestly reduced average normalized error across 21 other measurements; clinical importance remains uncertain.",
              40, 74, 735, size=13.7, leading=17.5, max_height=54)
    text(815, 87, "Interpretation and limitations", 20, bold=True)
    paragraph("Single-dataset revalidation of a previously examined test split. Report values were the reference, without independent remeasurement. Acquisition details and image-annotation cues remain incompletely verified. Accuracy for unreported values and clinical use is not established.",
              815, 74, 745, size=12.7, leading=16, max_height=49)
    text(40, 8, "[1] Vukadinovic et al. Nature 2026. doi:10.1038/s41586-025-09850-x.  [2] Data: MIMIC-IV-ECHO v1.0, PhysioNet. doi:10.13026/nrjh-5r77.", 9.4, MUTED)
    canvas.linkURL("https://doi.org/10.1038/s41586-025-09850-x", (40, 6, 505, 20), relative=0, thickness=0)
    canvas.linkURL("https://doi.org/10.13026/nrjh-5r77", (509, 6, 1050, 20), relative=0, thickness=0)
    canvas.showPage(); canvas.save()
    body = buffer.getvalue()
    pdf = PdfReader(io.BytesIO(body))
    base.require(len(pdf.pages) == 1 and tuple(map(float, pdf.pages[0].mediabox)) == (0., 0., WIDTH, HEIGHT),
                 "POSTER_SINGLE_16_9_PAGE_REQUIRED")
    extracted = " ".join(pdf.pages[0].extract_text().split())
    base.require(all(label in extracted for label in base.LABELS.values())
                 and all(a["name"] in extracted for a in authors["authors"])
                 and "DRAFT" not in extracted and "Aggregate bundle" not in extracted
                 and "selected patients had no cine data" not in extracted and "Panel scaling:" not in extracted
                 and "NOT FOR SUBMISSION" not in extracted, "POSTER_COMPLETE_TEXT_REQUIRED")
    base.require(not output.exists() and not output.is_symlink(), "POSTER_OUTPUT_ALREADY_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(body)
    return {"status": "PASS_POSTER_RENDERED_VISUAL_REVIEW_REQUIRED", "pdf_sha256": hashlib.sha256(body).hexdigest(),
            "pdf_bytes": len(body), "pages": 1, "aspect_ratio": "16:9", "bundle_sha256": bundle_sha256,
            "supplement_sha256": supplement_sha256, "authors_sha256": authors_sha256,
            "patient_level_data_read": False, "model_refitted": False, "bootstrap_recomputed": False,
            "display_conversion": {"lvot_vti": "mm to cm; divide all three MAEs by 10"},
            "display_order": "Clinical groups; source strict_panel order unchanged",
            "scaled_mae_definition": "Existing normalized_mae = native MAE / training IQR; dimensionless; no display-unit conversion",
            "header_logos": logo_placements,
            "rendered_projection": projection, "conference_uploaded": False}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "supplement", "authors", "output", "receipt", "bmc-logo", "bu-logo"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("bundle", "supplement", "authors", "bmc-logo", "bu-logo"):
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--font-directory", type=Path)
    args = parser.parse_args()
    base.require(not args.receipt.exists() and not args.receipt.is_symlink(), "POSTER_RECEIPT_ALREADY_EXISTS")
    candidate, supplement = base.validated_inputs(args.bundle, args.supplement,
        bundle_sha256=args.bundle_sha256, supplement_sha256=args.supplement_sha256)
    authors = read_authors(args.authors, args.authors_sha256)
    logos = {"bmc": read_logo(args.bmc_logo, args.bmc_logo_sha256),
             "bu": read_logo(args.bu_logo, args.bu_logo_sha256)}
    result = render(candidate, supplement, authors, output=args.output, font_directory=args.font_directory,
        bundle_sha256=args.bundle_sha256, supplement_sha256=args.supplement_sha256,
        authors_sha256=args.authors_sha256, logos=logos)
    result["builder_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["validated_reader_sha256"] = hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest()
    with os.fdopen(os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(base.canonical(result))
    print(json.dumps({k: v for k, v in result.items() if k != "rendered_projection"}, sort_keys=True))


if __name__ == "__main__":
    main()
