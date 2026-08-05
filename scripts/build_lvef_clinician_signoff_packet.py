#!/usr/bin/env python3
"""Build and validate the fixed eight-question SCC clinician signoff packet."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import load_table, require_restricted_path, run_guarded
from lvef_multitask_analysis_modes import (
    bind_approved_restricted_path,
    load_policy as load_safe_export_policy,
)


CLINICAL_ISSUE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "issue_id": "ARCH_DIAM_LEVEL",
        "targets": ("arch_diam",),
        "prompt": "Which named aortic-arch level and measurement convention does this project field encode?",
        "options": (
            "PROXIMAL_ARCH",
            "TRANSVERSE_ARCH",
            "DISTAL_ARCH_OR_ISTHMUS",
            "MIXED_ARCH_LEVELS",
            "OTHER_EXPLICIT_CONSTRUCT",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls target identity, family masking, and whether the field may be scored as one coherent task.",
    },
    {
        "issue_id": "ASCENDING_AORTA_CONVENTION",
        "targets": ("ascending_aorta_diameter",),
        "prompt": "Which tubular ascending-aorta segment, edge convention, and cardiac timing does this project field encode?",
        "options": (
            "LEADING_EDGE_END_DIASTOLIC",
            "INNER_EDGE_END_DIASTOLIC",
            "ANOTHER_EXPLICIT_CONVENTION",
            "MIXED_CONVENTIONS",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls alias handling, the aortic family mask, and target-scoring eligibility.",
    },
    {
        "issue_id": "INF_LAT_THICKNESS_DEFINITION",
        "targets": ("inf_lat_thickness",),
        "prompt": "Which wall, phase, and acquisition convention does this project field encode?",
        "options": (
            "END_DIASTOLIC_POSTERIOR_WALL",
            "END_DIASTOLIC_INFEROLATERAL_WALL",
            "ANOTHER_EXPLICIT_WALL_CONSTRUCT",
            "MIXED_CONSTRUCTS",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls whether aliases can be harmonized, which LV-geometry predictors are masked, and whether the task is scoreable.",
    },
    {
        "issue_id": "IVC_DIAM_CONTEXT",
        "targets": ("ivc_diam",),
        "prompt": "Which respiratory phase, ventilation context, and collapse information define this project field?",
        "options": (
            "END_EXPIRATORY_WITH_COLLAPSE_CONTEXT",
            "END_EXPIRATORY_DIAMETER_ONLY",
            "ANOTHER_EXPLICIT_RESPIRATORY_CONTEXT",
            "MIXED_PHASE_OR_VENTILATION_CONTEXT",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls IVC/RAP-family masks, interpretation of related fields, and target-scoring eligibility.",
    },
    {
        "issue_id": "LA_DIMEN_PLANE",
        "targets": ("la_dimen",),
        "prompt": "Which anatomic plane, linear axis, and timing define this project field?",
        "options": (
            "PLAX_ANTEROPOSTERIOR_AT_LV_END_SYSTOLE",
            "ANOTHER_EXPLICIT_PLANE_AND_TIMING",
            "MIXED_PLANES_OR_TIMINGS",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls LA-family masking, alias treatment, and whether the task represents a coherent measurement.",
    },
    {
        "issue_id": "MITRAL_E_FIELD_RELATIONSHIP",
        "targets": ("mv_peak_e", "mitral_e_velocity"),
        "prompt": "After considering acquisition site, Doppler mode, timing, and unit—not wording alone—how are these two project fields related?",
        "options": (
            "SAME_CONSTRUCT_SAME_UNIT",
            "SAME_CONSTRUCT_AFTER_PRESPECIFIED_UNIT_CONVERSION",
            "DISTINCT_ACQUISITION_CONSTRUCTS",
            "MIXED_OR_OVERLAPPING_CONSTRUCTS",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls merge prohibition or approval, mitral-diastolic family masks, and whether one or both tasks may be scored.",
    },
    {
        "issue_id": "SINUS_DIAM_CONVENTION",
        "targets": ("sinus_diam",),
        "prompt": "Which sinus-of-Valsalva edge convention and cardiac timing does this project field encode?",
        "options": (
            "LEADING_EDGE_END_DIASTOLIC",
            "INNER_EDGE_END_DIASTOLIC",
            "ANOTHER_EXPLICIT_CONVENTION",
            "MIXED_CONVENTIONS",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls aortic-family masks, alias treatment, and target-scoring eligibility.",
    },
    {
        "issue_id": "TR_MMHG_DEFINITION",
        "targets": ("tr_mmhg",),
        "prompt": "Which pressure construct is represented, accounting for whether right-atrial pressure is included?",
        "options": (
            "PEAK_TR_OR_RV_RA_GRADIENT_WITHOUT_RAP",
            "RVSP_OR_PASP_INCLUDING_RAP",
            "ANOTHER_EXPLICIT_PRESSURE_CONSTRUCT",
            "MIXED_PRESSURE_CONSTRUCTS",
            "UNRESOLVED_EXCLUDE",
        ),
        "consequence": "The answer controls formula edges, TR/RAP/pulmonary-pressure family masks, and whether the task can be scored.",
    },
)

CLINICAL_ISSUE_IDS = tuple(spec["issue_id"] for spec in CLINICAL_ISSUE_SPECS)
METADATA_COLUMNS: tuple[str, ...] = (
    "allowlisted_target",
    "raw_name",
    "raw_description",
    "native_unit",
    "normalized_unit",
    "canonical_source",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _escape(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _metadata_group(rows: pd.DataFrame, targets: tuple[str, ...]) -> pd.DataFrame:
    missing = set(METADATA_COLUMNS) - set(rows.columns)
    if missing:
        raise ValueError(f"Clinical review rows are missing columns: {sorted(missing)}")
    group = rows[rows["allowlisted_target"].isin(targets)].loc[:, METADATA_COLUMNS].drop_duplicates()
    if set(group["allowlisted_target"]) != set(targets):
        raise ValueError("At least one fixed clinician-question target has no exact metadata row")
    return group


def option_consequence(option: str) -> str:
    if option == "UNRESOLVED_EXCLUDE":
        return (
            "Do not merge aliases; apply the conservative unresolved-family mask; "
            "exclude the target from all scored panels."
        )
    if option.startswith("MIXED_") or option == "MIXED_OR_OVERLAPPING_CONSTRUCTS":
        return (
            "Do not merge aliases; mask every implicated shortcut family; exclude from scored panels "
            "unless a separately prespecified stratified definition becomes possible."
        )
    if option.startswith("SAME_CONSTRUCT"):
        return (
            "Permit alias merging only after a separate technical unit/value-identity audit; use one joint "
            "family mask; score one construct only after all remaining support gates pass."
        )
    if option == "DISTINCT_ACQUISITION_CONSTRUCTS":
        return (
            "Prohibit alias merging; retain separate exact-target masks within the coherent clinical family; "
            "score separately only after unit and support gates pass."
        )
    return (
        "Record this exact construct without automatically merging aliases; apply the adjudicated clinical-family "
        "mask; permit scoring only after unit, support, and registry gates pass."
    )


def build_packet(rows: pd.DataFrame, source_commit: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Source commit must be a full lowercase Git SHA")
    lines = [
        "# Restricted echocardiographer signoff packet",
        "",
        f"Source commit: `{source_commit}`",
        "",
        "This packet contains restricted project metadata and must remain on SCC. It asks for construct identity, not an inference from wording or a comparison of patient values. Select exactly one option per question. `UNRESOLVED_EXCLUDE` is an acceptable scientific decision.",
        "",
        "Reviewer name or initials: ____________________",
        "",
        "Role / echocardiographic expertise: ____________________",
        "",
        "Echo measurement expertise attestation: [ ] I have appropriate expertise to adjudicate these measurement definitions.",
        "",
        "Signoff date (YYYY-MM-DD): ____________________",
        "",
    ]
    for index, spec in enumerate(CLINICAL_ISSUE_SPECS, start=1):
        lines.extend(
            [
                f"## Q{index}. `{spec['issue_id']}`",
                "",
                f"Exact project canonical name(s): {', '.join(f'`{target}`' for target in spec['targets'])}",
                "",
                str(spec["prompt"]),
                "",
                "| Exact canonical name | Exact raw name | Exact raw description | Recorded native unit | Normalized unit | Mapping source |",
                "|---|---|---|---|---|---|",
            ]
        )
        for _, row in _metadata_group(rows, spec["targets"]).iterrows():
            lines.append("| " + " | ".join(_escape(row[column]) for column in METADATA_COLUMNS) + " |")
        lines.extend(["", "Select exactly one:", ""])
        lines.extend(
            f"- [ ] `{option}` — {option_consequence(option)}"
            for option in spec["options"]
        )
        lines.extend(
            [
                "",
                f"Decision consequence: {spec['consequence']}",
                "",
                "Rationale (required; do not rely on wording alone):",
                "",
                "________________________________________________________________________________",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def response_template(packet_sha256: str) -> dict[str, Any]:
    return {
        "packet_sha256": packet_sha256,
        "reviewer": {
            "name_or_initials": "",
            "role_expertise": "",
            "echo_measurement_expertise_attested": False,
            "signoff_date": "",
        },
        "responses": [
            {
                "issue_id": spec["issue_id"],
                "selected_option": "",
                "rationale": "",
            }
            for spec in CLINICAL_ISSUE_SPECS
        ],
    }


def validate_response(packet_path: Path, response: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    packet_checksum = sha256_file(packet_path)
    if response.get("packet_sha256") != packet_checksum:
        issues.append("PACKET_CHECKSUM_MISMATCH")

    reviewer = response.get("reviewer")
    if not isinstance(reviewer, dict):
        issues.append("REVIEWER_RECORD_MISSING")
        reviewer = {}
    if not str(reviewer.get("name_or_initials", "")).strip():
        issues.append("REVIEWER_NAME_OR_INITIALS_MISSING")
    if not str(reviewer.get("role_expertise", "")).strip():
        issues.append("REVIEWER_ROLE_EXPERTISE_MISSING")
    if reviewer.get("echo_measurement_expertise_attested") is not True:
        issues.append("ECHO_MEASUREMENT_EXPERTISE_NOT_ATTESTED")
    signoff_date = str(reviewer.get("signoff_date", ""))
    try:
        date.fromisoformat(signoff_date)
    except ValueError:
        issues.append("SIGNOFF_DATE_INVALID")

    responses = response.get("responses")
    if not isinstance(responses, list):
        responses = []
        issues.append("RESPONSES_NOT_A_LIST")
    response_ids = [item.get("issue_id") for item in responses if isinstance(item, dict)]
    if len(response_ids) != len(CLINICAL_ISSUE_IDS) or set(response_ids) != set(CLINICAL_ISSUE_IDS):
        issues.append("RESPONSE_ISSUE_SET_MISMATCH")
    if len(response_ids) != len(set(response_ids)):
        issues.append("DUPLICATE_RESPONSE_ISSUE_ID")

    specs = {spec["issue_id"]: spec for spec in CLINICAL_ISSUE_SPECS}
    unresolved_count = 0
    for item in responses:
        if not isinstance(item, dict) or item.get("issue_id") not in specs:
            continue
        spec = specs[item["issue_id"]]
        choice = item.get("selected_option")
        if choice not in spec["options"]:
            issues.append(f"INVALID_OR_MISSING_OPTION:{item['issue_id']}")
        if choice == "UNRESOLVED_EXCLUDE":
            unresolved_count += 1
        if not str(item.get("rationale", "")).strip():
            issues.append(f"RATIONALE_MISSING:{item['issue_id']}")

    return {
        "audit": "lvef_multitask_clinician_signoff_validation",
        "status": "PASS" if not issues else "FAIL",
        "signoff_complete": not issues,
        "packet_checksum_verified": response.get("packet_sha256") == packet_checksum,
        "n_expected_questions": len(CLINICAL_ISSUE_IDS),
        "n_responses": len(responses),
        "n_unresolved_exclude": unresolved_count,
        "n_validation_issues": len(issues),
        "validation_issues": issues,
        "reviewer_identity_exported": False,
        "restricted_metadata_exported": False,
    }


def build_command(args: argparse.Namespace) -> int:
    safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
    review_rows_path = bind_approved_restricted_path(
        args.clinical_review_rows_csv,
        policy=safe_policy,
        must_exist=True,
        expect="file",
    )
    output_dir = bind_approved_restricted_path(
        args.output_dir,
        policy=safe_policy,
        must_exist=False,
        expect="directory",
        root_kind="direct",
        create=True,
    )
    if any(output_dir.iterdir()):
        raise ValueError("Clinician packet output directory must be empty")
    rows = load_table(review_rows_path)
    packet_path = output_dir / "clinical_metadata_clinician_signoff_restricted.md"
    packet_path.write_text(build_packet(rows, args.source_commit))
    packet_sha256 = sha256_file(packet_path)
    response_path = output_dir / "clinical_metadata_clinician_response_restricted.json"
    response_path.write_text(json.dumps(response_template(packet_sha256), indent=2, sort_keys=True) + "\n")
    manifest = {
        "audit": "lvef_multitask_clinician_packet",
        "status": "READY_FOR_HUMAN_SIGNOFF",
        "packet_sha256": packet_sha256,
        "source_commit": args.source_commit,
        "n_questions": len(CLINICAL_ISSUE_IDS),
        "question_ids": list(CLINICAL_ISSUE_IDS),
        "requires_echocardiographer_or_echo_measurement_expert": True,
        "human_signoff_complete": False,
        "restricted_output_export_authorized": False,
    }
    (output_dir / "clinical_metadata_clinician_packet_manifest_restricted.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "n_questions": len(CLINICAL_ISSUE_IDS),
                "human_signoff_complete": False,
                "restricted_metadata_printed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def validate_command(args: argparse.Namespace) -> int:
    safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
    packet_path = bind_approved_restricted_path(
        args.packet_md,
        policy=safe_policy,
        must_exist=True,
        expect="file",
    )
    response_path = bind_approved_restricted_path(
        args.response_json,
        policy=safe_policy,
        must_exist=True,
        expect="file",
    )
    response = json.loads(response_path.read_text())
    result = validate_response(packet_path, response)
    if args.validation_output_json:
        output = bind_approved_restricted_path(
            args.validation_output_json,
            policy=safe_policy,
            must_exist=False,
            expect="file",
            root_kind="direct",
            create=True,
        )
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["signoff_complete"] else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--clinical-review-rows-csv", type=Path, required=True)
    build.add_argument("--source-commit", required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument(
        "--safe-export-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_multitask_safe_export_policy.yaml",
    )
    validate = subparsers.add_parser("validate")
    validate.add_argument("--packet-md", type=Path, required=True)
    validate.add_argument("--response-json", type=Path, required=True)
    validate.add_argument("--validation-output-json", type=Path)
    validate.add_argument(
        "--safe-export-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_multitask_safe_export_policy.yaml",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return build_command(args) if args.command == "build" else validate_command(args)


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
