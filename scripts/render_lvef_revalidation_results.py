"""Render pending templates or a complete, safety-reviewed aggregate bundle.

No fitting, prediction, restricted input discovery, or conference upload. The
candidate projector removes row-level material; it does not grant export access.
Only a separate safety receipt binding the exact candidate enables result plots.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from lvef_revalidation_analysis import EVALUATION_CONDITIONS, MODALITIES, canonical_bytes, digest, require
from lvef_revalidation_inference import CORE_CLAIMS

CONDITIONS = EVALUATION_CONDITIONS
LABELS = {"vision_only": "Vision only", "structured_only": "Structured only", "early_fusion": "Early fusion"}
COLORS = {"vision_only": "#284b70", "structured_only": "#287c79", "early_fusion": "#b95c39"}
NAME = re.compile(r"[a-z][a-z0-9_]{0,95}")
SHA = re.compile(r"[0-9a-f]{64}")
METRICS = ("mae", "normalized_mae", "rmse", "r2", "mean_signed_error", "pearson_correlation",
           "spearman_correlation", "calibration_intercept", "calibration_slope")
FORBIDDEN = {"subject_id", "subject_ids", "study_id", "study_ids", "target_values", "prediction", "predictions",
             "score", "calibrated_probability", "uncalibrated_probability", "test_subject_roster", "restricted_path"}


def _no_rows(value: Any) -> None:
    if isinstance(value, dict):
        require(not FORBIDDEN & set(value), "RENDER_RESTRICTED_FIELDS_REFUSED")
        for child in value.values():
            _no_rows(child)
    elif isinstance(value, list):
        for child in value:
            _no_rows(child)


def _interval(value: Mapping[str, Any], *, model: bool = False) -> dict[str, Any]:
    point = value.get("estimate" if model else "effect")
    ci = value.get("interval")
    require(point is None or type(point) in {int, float} and math.isfinite(point), "RENDER_ESTIMATE_INVALID")
    require(ci is None or len(ci) == 2 and all(type(x) in {int, float} and math.isfinite(x) for x in ci) and ci[0] <= ci[1], "RENDER_INTERVAL_INVALID")
    require(value["replicates"] == 10000 and type(value["valid_replicates"]) is int
            and type(value["undefined_replicates"]) is int
            and value["valid_replicates"] + value["undefined_replicates"] == 10000
            and min(value["valid_replicates"], value["undefined_replicates"]) >= 0, "RENDER_BOOTSTRAP_INCOMPLETE")
    result = {"estimate" if model else "effect": point, "interval": ci,
              "valid_replicates": value["valid_replicates"], "undefined_replicates": value["undefined_replicates"], "replicates": 10000}
    if not model:
        p = value["p_value"]
        require(p is None or type(p) in {int, float} and math.isfinite(p) and 0 <= p <= 1, "RENDER_PVALUE_INVALID")
        result["p_value"] = p
    return result


def extract_aggregate_bundle(evaluation: Mapping[str, Any], paired_report: Mapping[str, Any], *,
                             input_audit: Mapping[str, Any], safety_authority: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Pure projection. Caller first replays the source receipt/file hash graph."""
    require(evaluation["status"] == "PASS_LOCKED_TEST_EVALUATION" and evaluation["fixed_models"] is True
            and evaluation["strict_panel_locked"] is True and evaluation["construct"] == "strict"
            and paired_report["status"] == "PASS_PRIVATE_PAIRED_REPORT"
            and paired_report["spec_sha256"] == evaluation["spec_sha256"]
            and set(paired_report["conditions"]) == set(CONDITIONS), "RENDER_COMPLETE_VALIDATED_REPORT_REQUIRED")
    panel = list(evaluation["strict_panel"])
    require(panel and "lvef" not in panel and len(set(panel)) == len(panel)
            and set(evaluation["targets"]) == {"lvef", *panel}, "RENDER_LOCKED_PANEL_MISMATCH")
    require(input_audit["artifact_type"] == "lvef_revalidation_inputs_v1"
            and input_audit["all_missing_allowed_structured_rows_retained"] is True
            and input_audit["lvef_common_counts"] == evaluation["targets"]["lvef"]["denominators"], "RENDER_INPUT_FLOW_BINDING_MISMATCH")
    rows, effects, binary = [], [], []
    for condition in CONDITIONS:
        report = paired_report["conditions"][condition]
        secondary = report["secondary_intervals"]
        require(secondary["replicates"] == 10000 and secondary["condition"] == condition
                and set(secondary["targets"]) == {"lvef", *panel}, "RENDER_SECONDARY_REPORT_INCOMPLETE")
        for target in ("lvef", *panel):
            source = evaluation["targets"][target]
            require(NAME.fullmatch(target) and NAME.fullmatch(source["family"])
                    and re.fullmatch(r"[A-Za-z0-9%/ .^_-]{1,40}", source["unit"]), "RENDER_CLINICAL_METADATA_INVALID")
            counts = source["denominators"]
            require(set(counts) == {"train", "val", "test"} and all(type(n) is int and n >= 40 for n in counts.values()), "RENDER_DENOMINATOR_INVALID")
            intervals = secondary["targets"][target]["continuous"]
            for modality in MODALITIES:
                actual = "primary" if modality == "vision_only" else condition
                state = source["conditions"][actual][modality]
                metrics = {k: state["metrics"][k] for k in METRICS}
                require(all(type(metrics[k]) in {int, float} and math.isfinite(metrics[k]) for k in ("mae", "normalized_mae", "rmse")), "RENDER_PRIMARY_METRIC_UNDEFINED")
                model_intervals = {k: _interval(intervals["model_metrics"][modality][k], model=True) for k in METRICS}
                require(all(model_intervals[k]["estimate"] == metrics[k] for k in METRICS), "RENDER_MODEL_INTERVAL_BINDING_MISMATCH")
                rows.append({"condition": condition, "target": target, "unit": source["unit"], "family": source["family"],
                    "modality": modality, "denominators": dict(counts), "training_iqr": source["train_iqr"],
                    "common_row_sha256": source["row_sha256"],
                    "metrics": metrics, "metric_intervals": model_intervals,
                    "within_5_ef_points": state["metrics"].get("tolerance_coverage", {}).get("5.0"),
                    "information_burden_strata": state["information_burden_strata"]})
                if target == "lvef":
                    for endpoint, bs in state["binary"].items():
                        if bs["status"] != "EVALUATED":
                            binary.append({"condition": condition, "modality": modality, "endpoint": endpoint, "status": "UNSUPPORTED_CLASS_COUNTS"})
                            continue
                        bm = {k: v for k, v in bs["metrics"].items() if v is None or type(v) in {int, float}}
                        require(bm["events"] >= 20 and bm["nonevents"] >= 20, "RENDER_BINARY_SUPPORT_INVALID")
                        binary.append({"condition": condition, "modality": modality, "endpoint": endpoint, "status": "EVALUATED",
                            "metrics": bm, "frozen_cutoff": bs["frozen_cutoff"],
                            "auroc_interval": _interval(secondary["targets"][target]["binary"][endpoint]["model_metrics"][modality]["auroc"], model=True)})
            for contrast, metrics in intervals["contrasts"].items():
                effects.append({"condition": condition, "target": target, "unit": source["unit"], "contrast": contrast,
                                "mae": _interval(metrics["mae"]), "normalized_mae": _interval(metrics["normalized_mae"])})
    core = paired_report["conditions"]["primary"]["primary_inference"]["core_multiplicity"]
    require(core["family"] == "core_global_four_claim" and set(core["adjusted_p_values"]) == set(CORE_CLAIMS)
            and core["strict_target_count"] == len(panel), "RENDER_FOUR_CLAIM_CORE_REQUIRED")
    flow = {"counts": {k: input_audit["counts"][k] for k in ("selected_studies", "selected_subjects", "imaging_eligible", "no_cine", "clip_embeddings")},
            "selected_split_counts": dict(input_audit["selected_split_counts"]), "imaging_split_counts": dict(input_audit["imaging_split_counts"]),
            "lvef_common_counts": dict(input_audit["lvef_common_counts"]), "exact40_counts": dict(input_audit["exact40_counts"])}
    candidate = {"schema_version": 1, "artifact_type": "lvef_revalidation_aggregate_candidate_v1", "status": "CANDIDATE_AGGREGATE_RESULTS_NOT_RELEASED",
        "spec_sha256": evaluation["spec_sha256"], "frozen_sha256": evaluation["frozen_sha256"],
        "evaluation_sha256": digest(evaluation), "report_sha256": digest(paired_report), "input_audit_sha256": digest(input_audit),
        "strict_panel": panel, "conditions": list(CONDITIONS), "rows": rows, "paired_effects": effects, "binary": binary,
        "core_holm": dict(core["adjusted_p_values"]), "flow": flow,
        "panel_summary": {c: paired_report["conditions"][c]["panel_summary"] for c in CONDITIONS},
        "poster_export_authorized": False, "contains_patient_level_data": False}
    validate_candidate(candidate)
    return seal_aggregate_bundle(candidate, safety_authority) if safety_authority is not None else candidate


def validate_candidate(candidate: Mapping[str, Any]) -> None:
    expected = {"schema_version", "artifact_type", "status", "spec_sha256", "frozen_sha256", "evaluation_sha256", "report_sha256",
                "input_audit_sha256", "strict_panel", "conditions", "rows", "paired_effects", "binary", "core_holm", "flow", "panel_summary",
                "poster_export_authorized", "contains_patient_level_data"}
    require(set(candidate) == expected and candidate["schema_version"] == 1
            and candidate["artifact_type"] == "lvef_revalidation_aggregate_candidate_v1"
            and candidate["status"] == "CANDIDATE_AGGREGATE_RESULTS_NOT_RELEASED"
            and candidate["poster_export_authorized"] is False and candidate["contains_patient_level_data"] is False,
            "RENDER_CANDIDATE_SCHEMA_INVALID")
    require(all(SHA.fullmatch(candidate[k]) for k in expected if k.endswith("sha256")), "RENDER_CANDIDATE_HASH_INVALID")
    _no_rows(dict(candidate))
    panel = candidate["strict_panel"]
    require(panel and len(set(panel)) == len(panel) and "lvef" not in panel and all(NAME.fullmatch(x) for x in panel)
            and candidate["conditions"] == list(CONDITIONS), "RENDER_PANEL_INVALID")
    require(len(candidate["rows"]) == len(CONDITIONS) * 3 * (1 + len(panel)), "RENDER_MISSING_TASK_OR_MODALITY")
    expected_order = [(c, t, m) for c in CONDITIONS for t in ("lvef", *panel) for m in MODALITIES]
    require([(r["condition"], r["target"], r["modality"]) for r in candidate["rows"]] == expected_order, "RENDER_PANEL_ORDER_OR_ROWS_CHANGED")
    require(set(candidate["core_holm"]) == set(CORE_CLAIMS)
            and all(type(p) in {float, int} and math.isfinite(p) and 0 <= p <= 1 for p in candidate["core_holm"].values()), "RENDER_CORE_FAMILY_INVALID")
    for row in candidate["rows"]:
        require(set(row) == {"condition", "target", "unit", "family", "modality", "denominators", "training_iqr", "common_row_sha256", "metrics", "metric_intervals", "within_5_ef_points", "information_burden_strata"}
                and set(row["metrics"]) == set(METRICS) and set(row["metric_intervals"]) == set(METRICS), "RENDER_TABLE_SCHEMA_INVALID")
        require(SHA.fullmatch(row["common_row_sha256"]) and NAME.fullmatch(row["family"])
                and re.fullmatch(r"[A-Za-z0-9%/ .^_-]{1,40}", row["unit"]), "RENDER_METADATA_INVALID")
        require(all(v is None or type(v) in {int, float} and math.isfinite(v) for v in row["metrics"].values()), "RENDER_TABLE_VALUE_INVALID")
        for key in METRICS:
            _interval(row["metric_intervals"][key], model=True)
            require(row["metric_intervals"][key]["estimate"] == row["metrics"][key], "RENDER_MODEL_INTERVAL_BINDING_MISMATCH")
    for condition in CONDITIONS:
        for target in ("lvef", *panel):
            rows = [r for r in candidate["rows"] if r["condition"] == condition and r["target"] == target]
            require(all(r["denominators"] == rows[0]["denominators"] and r["training_iqr"] == rows[0]["training_iqr"]
                        and r["common_row_sha256"] == rows[0]["common_row_sha256"] for r in rows), "RENDER_COMMON_DENOMINATOR_MISMATCH")
    flow = candidate["flow"]
    require(set(flow) == {"counts", "selected_split_counts", "imaging_split_counts", "lvef_common_counts", "exact40_counts"}, "RENDER_FLOW_SCHEMA_INVALID")
    for key in ("selected_split_counts", "imaging_split_counts", "lvef_common_counts", "exact40_counts"):
        require(set(flow[key]) == {"train", "val", "test"} and all(type(v) is int and v >= 0 for v in flow[key].values()), "RENDER_FLOW_COUNTS_INVALID")
    require(sum(flow["selected_split_counts"].values()) == flow["counts"]["selected_subjects"]
            and sum(flow["imaging_split_counts"].values()) == flow["counts"]["imaging_eligible"], "RENDER_FLOW_TOTAL_MISMATCH")
    for row in candidate["paired_effects"]:
        require(set(row) == {"condition", "target", "unit", "contrast", "mae", "normalized_mae"}, "RENDER_EFFECT_SCHEMA_INVALID")
        _interval(row["mae"]); _interval(row["normalized_mae"])
    require(len(candidate["paired_effects"]) == len(candidate["rows"]), "RENDER_PAIRED_EFFECTS_INCOMPLETE")
    expected_effects = {(c, t, contrast) for c in CONDITIONS for t in ("lvef", *panel)
                        for contrast in ("early_fusion_minus_vision_only", "early_fusion_minus_structured_only", "structured_only_minus_vision_only")}
    require({(r["condition"], r["target"], r["contrast"]) for r in candidate["paired_effects"]} == expected_effects, "RENDER_PAIRED_EFFECT_SET_MISMATCH")
    canonical_bytes(candidate)  # No NaN/Infinity hiding in a nested aggregate.


def seal_aggregate_bundle(candidate: Mapping[str, Any], safety_authority: Mapping[str, Any]) -> dict[str, Any]:
    validate_candidate(candidate)
    require(set(safety_authority) == {"status", "candidate_sha256", "safety_receipt_sha256"}
            and safety_authority["status"] == "PASS_REVALIDATION_AGGREGATE_SAFETY"
            and safety_authority["candidate_sha256"] == digest(candidate)
            and SHA.fullmatch(safety_authority["safety_receipt_sha256"]), "RENDER_SAFETY_AUTHORITY_REQUIRED")
    return {"schema_version": 1, "artifact_type": "lvef_revalidation_validated_aggregate_bundle_v1",
            "status": "PASS_VALIDATED_COMPLETE_AGGREGATE_BUNDLE", "candidate": dict(candidate),
            "candidate_sha256": digest(candidate), "aggregate_safety": dict(safety_authority), "poster_export_authorized": False}


def validate_bundle(value: Mapping[str, Any]) -> Mapping[str, Any]:
    require(set(value) == {"schema_version", "artifact_type", "status", "candidate", "candidate_sha256", "aggregate_safety", "poster_export_authorized"}, "RENDER_VALIDATED_BUNDLE_REQUIRED")
    expected = seal_aggregate_bundle(value["candidate"], value["aggregate_safety"])
    require(canonical_bytes(value) == canonical_bytes(expected), "RENDER_BUNDLE_BINDING_MISMATCH")
    return value["candidate"]


def _plotting():
    if "MPLCONFIGDIR" not in os.environ:
        os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp(prefix="lvef-render-mpl-")
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.hashsalt": "lvef-asa-2026-v1",
        "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    return plt, FancyBboxPatch


def _box(ax, text, xy, *, width=.38, height=.12, color="#eef3f7", fontsize=11):
    _, Patch = _plotting()
    x, y = xy
    ax.add_patch(Patch((x, y), width, height, boxstyle="round,pad=0.012", linewidth=.8, edgecolor="#738496", facecolor=color))
    ax.text(x + width / 2, y + height / 2, text, ha="center", va="center", fontsize=fontsize, linespacing=1.5)


def _publish(path: Path, body: bytes) -> None:
    if path.exists():
        require(path.is_file() and not path.is_symlink() and path.read_bytes() == body, "RENDER_OUTPUT_ALREADY_EXISTS")
        return
    with path.open("xb") as stream:
        stream.write(body)


def _save(fig, root: Path, name: str, *, preview_root: Path | None = None) -> list[dict[str, Any]]:
    plt, _ = _plotting()
    result = []
    for extension in ("svg", "pdf"):
        buffer = io.BytesIO()
        metadata = {"Date": None} if extension == "svg" else {"CreationDate": None, "ModDate": None}
        fig.savefig(buffer, format=extension, bbox_inches="tight", facecolor="white", metadata=metadata)
        body = buffer.getvalue()
        path = root / f"{name}.{extension}"
        _publish(path, body)
        result.append({"name": path.name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    if preview_root is not None:
        preview_root.mkdir(parents=True, exist_ok=True)
        fig.savefig(preview_root / f"{name}.png", dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return result


def _frame(title: str, subtitle: str, *, height=6):
    plt, _ = _plotting()
    fig, ax = plt.subplots(figsize=(11, height))
    fig.suptitle(title, x=.07, ha="left", y=.98, fontsize=19, color="#173b5a", weight="bold")
    fig.text(.07, .90, subtitle, fontsize=10, color="#52616e")
    fig.subplots_adjust(top=.83, bottom=.12, left=.08, right=.96)
    return fig, ax


def render(output: Path, *, bundle: Mapping[str, Any] | None = None, preview_root: Path | None = None) -> dict[str, Any]:
    candidate = validate_bundle(bundle) if bundle is not None else None
    require(not output.is_symlink(), "RENDER_OUTPUT_SYMLINK")
    output.mkdir(parents=True, exist_ok=True)
    pending = candidate is None
    suffix = "pending" if pending else "validated"
    note = "PENDING — template only; no model results have been inserted." if pending else "Validated aggregate results · frozen encoder with supervised downstream models"
    artifacts = []
    fig, ax = _frame("Cohort and target-specific analysis flow", note, height=7)
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis("off")
    flow = candidate["flow"] if candidate else None
    stages = ["Selected one-study-per-subject cohort", "Imaging-eligible canonical study store", "Observed target + verified units / aggregation", "Exact common rows across all three modalities"]
    values = [f"{flow['counts']['selected_studies']:,} studies / {flow['counts']['selected_subjects']:,} subjects",
              f"{flow['counts']['imaging_eligible']:,} studies; {flow['counts']['no_cine']:,} prespecified no-cine exclusions",
              "LVEF common labels: " + f"{sum(flow['lvef_common_counts'].values()):,}",
              "Train / validation / test: " + " / ".join(f"{flow['lvef_common_counts'][s]:,}" for s in ("train", "val", "test"))] if flow else ["PENDING input authority", "PENDING imaging denominator", "PENDING target-specific label audit", "PENDING locked common denominator"]
    for i, (stage, value) in enumerate(zip(stages, values)):
        y = .80 - i * .21
        _box(ax, stage + "\n" + value, (.12, y), width=.76, height=.14)
        if i < 3:
            ax.annotate("", xy=(.5, y - .065), xytext=(.5, y - .01), arrowprops={"arrowstyle": "->", "color": "#536b80"})
    fig.text(.08, .055, "No-cine studies are excluded from every primary modality. All-missing allowed context stays eligible.\nTarget missingness is task-specific; observed-label completion does not validate naturally missing truth.", fontsize=9)
    artifacts += _save(fig, output, "cohort_flow." + suffix, preview_root=preview_root)

    fig, ax = _frame("LVEF: matched modality performance", note)
    ax.axis("off")
    columns = ["Modality", "Test n", "MAE [95% CI], EF points", "RMSE", "Error ≤5", "Logistic <40 AUROC"]
    table = []
    for modality in MODALITIES:
        if pending:
            table.append([LABELS[modality], *(["PENDING"] * 5)])
        else:
            row = next(r for r in candidate["rows"] if (r["condition"], r["target"], r["modality"]) == ("primary", "lvef", modality))
            binary = next(r for r in candidate["binary"] if (r["condition"], r["endpoint"], r["modality"]) == ("primary", "lvef_lt_40", modality))
            ci = row["metric_intervals"]["mae"]["interval"]
            table.append([LABELS[modality], str(row["denominators"]["test"]), f"{row['metrics']['mae']:.2f} [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "Undefined interval",
                          f"{row['metrics']['rmse']:.2f}", f"{100 * row['within_5_ef_points']:.1f}%", f"{binary['metrics']['auroc']:.3f}"])
    artist = ax.table(cellText=table, colLabels=columns, cellLoc="center", loc="center", colWidths=[.18, .10, .28, .10, .12, .22])
    artist.auto_set_font_size(False); artist.set_fontsize(9); artist.scale(1, 3)
    for (r, _), cell in artist.get_celld().items():
        cell.set_edgecolor("#d4dee6"); cell.set_facecolor("#eaf0f5" if r == 0 else "white")
    fig.text(.08, .075, "Continuous MAE is the primary anchor. Separately trained logistic AUROC is the primary binary metric.\nModel-specific intervals do not establish paired superiority. Calibration reuses validation after selection.", fontsize=9)
    artifacts += _save(fig, output, "lvef_modality_table." + suffix, preview_root=preview_root)

    fig, ax = _frame("LVEF: paired fusion effects", note, height=5)
    fig.subplots_adjust(bottom=.24)
    contrasts = ["early_fusion_minus_vision_only", "early_fusion_minus_structured_only"]
    ax.set_yticks([1, 0], ["Fusion − vision", "Fusion − structured"])
    ax.set_ylim(-.65, 1.65)
    if pending:
        ax.set_xticks([]); ax.set_xlim(0, 1)
        for y in (1, 0):
            ax.text(.5, y, "PENDING paired effect and interval", ha="center", color="#536b80")
    else:
        ax.axvline(0, color="#536b80", linewidth=1)
        for x in (-1, 1):
            ax.axvline(x, color="#cad2d9", linestyle="--", linewidth=.8)
        for y, contrast in zip((1, 0), contrasts):
            row = next(r for r in candidate["paired_effects"] if (r["condition"], r["target"], r["contrast"]) == ("primary", "lvef", contrast))
            value = row["mae"]
            if value["interval"] is None:
                ax.text(.5, y, "Undefined paired interval", transform=ax.get_yaxis_transform(), ha="center")
            else:
                ax.plot(value["interval"], [y, y], color=COLORS["early_fusion"], linewidth=3)
                ax.scatter([value["effect"]], [y], color=COLORS["early_fusion"], s=55, zorder=3)
    ax.set_xlabel("Fusion minus comparator MAE, EF percentage points · negative favors fusion")
    fig.text(.08, .035, "Paired subject bootstrap; fixed fitted models. Core Holm family contains all four LVEF + strict-panel claims.\nDashed ±1 EF-point guides, when populated, are expert-inference research margins, not MCID or clinical equivalence.", fontsize=9)
    artifacts += _save(fig, output, "lvef_paired_effects." + suffix, preview_root=preview_root)

    targets = candidate["strict_panel"] if candidate else []
    fig, ax = _frame("Complete locked-panel comparison", note, height=max(5, 2.5 + .36 * len(targets)))
    if pending:
        ax.axis("off")
        _box(ax, "PENDING human-adjudicated scored panel\nEvery locked target will remain visible, including unfavorable and undefined results.", (.04, .38), width=.92, height=.25)
    else:
        for j, target in enumerate(targets):
            for offset, modality in zip((-.2, 0, .2), MODALITIES):
                row = next(r for r in candidate["rows"] if (r["condition"], r["target"], r["modality"]) == ("primary", target, modality))
                ax.scatter(row["metrics"]["normalized_mae"], j + offset, color=COLORS[modality], s=25, label=LABELS[modality] if j == 0 else None)
        ax.set_yticks(range(len(targets)), [t.replace("_", " ") for t in targets], fontsize=8)
        ax.invert_yaxis(); ax.legend(loc="best", frameon=False, fontsize=9)
        ax.set_xlabel("MAE / training-target IQR · lower is better")
        fig.subplots_adjust(left=.39)
    fig.text(.08, .035, "Native-unit errors, denominators, paired intervals and families appear in the companion complete table.\nUnresolved non-LVEF margins prevent win/tie/loss labels; they do not prevent native-unit reporting.", fontsize=9)
    artifacts += _save(fig, output, "complete_task_panel." + suffix, preview_root=preview_root)

    fig, ax = _frame("Leakage masking and missingness analysis", "Prespecified workflow · no performance results or clinical adjudication implied", height=7)
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis("off")
    boxes = [("Remove target, aliases, duplicates,\nformulas and applicable family fields", (.03, .73)),
             ("Freeze exact common rows\nAll-missing allowed context retained", (.56, .73)),
             ("Training only: eligibility, median\nimputation, scaling and indicators", (.03, .46)),
             ("Shared transforms across modalities\nTrain supervised Ridge / logistic models", (.56, .46)),
             ("Validation only: grid selection,\nPlatt calibration and operating cutoff", (.03, .19)),
             ("Freeze artifacts → authorized test once\nPaired inference on unchanged subjects", (.56, .19))]
    for text, xy in boxes:
        _box(ax, text, xy, width=.41, height=.17, fontsize=10)
    for start, end in (((.44, .815), (.55, .815)), ((.765, .72), (.235, .64)), ((.44, .545), (.55, .545)), ((.765, .45), (.235, .37)), ((.44, .275), (.55, .275))):
        ax.annotate("", xy=end, xytext=start, arrowprops={"arrowstyle": "->", "color": "#536b80", "lw": 1})
    ax.text(.5, .05, "Mandatory: repeat structured and fusion models WITHOUT missingness indicators.\nRemoved fields have no path to feature eligibility, imputation or an indicator.", ha="center", fontsize=10, color="#8c3d28")
    artifacts += _save(fig, output, "masking_missingness." + suffix, preview_root=preview_root)

    if candidate:
        for filename, fields, records in _tables(candidate):
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(records)
            body = stream.getvalue().encode()
            _publish(output / filename, body)
            artifacts.append({"name": filename, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    manifest = {"schema_version": 1, "status": "PENDING_RESULT_TEMPLATES" if pending else "PASS_RENDERED_VALIDATED_AGGREGATES",
                "bundle_sha256": digest(bundle) if bundle is not None else None, "artifacts": artifacts,
                "invented_results": False, "patient_level_outputs": False, "poster_export_authorized": False}
    _publish(output / ("render_manifest." + suffix + ".json"), canonical_bytes(manifest))
    return manifest


def _tables(candidate):
    rows = []
    for row in candidate["rows"]:
        ci = row["metric_intervals"]["mae"]["interval"]
        rows.append({"condition": row["condition"], "target": row["target"], "family": row["family"], "unit": row["unit"],
            "modality": row["modality"], **{s + "_n": row["denominators"][s] for s in ("train", "val", "test")},
            "training_iqr": row["training_iqr"], **row["metrics"], "mae_ci_lower": ci[0] if ci else "UNDEFINED",
            "mae_ci_upper": ci[1] if ci else "UNDEFINED", "valid_bootstrap_replicates": row["metric_intervals"]["mae"]["valid_replicates"]})
    yield "complete_modality_results.validated.csv", list(rows[0]), rows
    rows = []
    for row in candidate["paired_effects"]:
        ci = row["mae"]["interval"]
        claim = "lvef_mae_" + row["contrast"]
        rows.append({"condition": row["condition"], "target": row["target"], "unit": row["unit"], "contrast": row["contrast"],
            "mae_difference": row["mae"]["effect"], "ci_lower": ci[0] if ci else "UNDEFINED", "ci_upper": ci[1] if ci else "UNDEFINED",
            "unadjusted_p": row["mae"]["p_value"], "core_holm_p": candidate["core_holm"].get(claim, "NOT_CORE") if row["condition"] == "primary" and row["target"] == "lvef" else "NOT_CORE",
            "valid_bootstrap_replicates": row["mae"]["valid_replicates"], "margin_status": "NOT_ASSESSED"})
    yield "paired_mae_effects.validated.csv", list(rows[0]), rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--templates", action="store_true")
    mode.add_argument("--bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview-root", type=Path)
    args = parser.parse_args(argv)
    bundle = json.loads(args.bundle.read_bytes()) if args.bundle else None
    result = render(args.output, bundle=bundle, preview_root=args.preview_root)
    print(json.dumps({"status": result["status"], "artifacts": len(result["artifacts"]), "invented_results": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
