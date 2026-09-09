#!/usr/bin/env python3
"""Validate existing aggregate reporting controls; never fit or load patient rows.

Use --inspect (default) first. Optional --output publishes one exclusive local
aggregate file; --chunk N emits a bounded gzip/base64 transport chunk. Neither
operation grants conference/public release or changes the bound analysis source.
"""
import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

MAX_PACKET_BYTES = 32 * 1024 * 1024
MAX_PACK_CHUNKS = 512
PACK_MARKER = "LVEF_AGGREGATE_CHUNK_V1"


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def exclusive_bytes(path, body):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    check(path.read_bytes() == body, "OUTPUT_READBACK_FAILED")


def packet_private_bytes(path, maximum):
    """Only immutable, hash-bound aggregate packet bytes; no analysis discovery."""
    import stat
    check(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)), "PACK_PATH_INVALID")
    parent = path.parent.stat()
    check(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700, "PACK_PARENT_NOT_PRIVATE")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        check(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o600
              and before.st_uid == os.getuid() and before.st_nlink == 1 and 0 < before.st_size <= maximum, "PACK_FILE_INVALID")
        body = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    attrs = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
    identity = lambda s: tuple(getattr(s, name) for name in attrs)
    check(identity(before) == identity(after) == identity(path.lstat()) and len(body) == before.st_size, "PACK_FILE_CHANGED")
    return body


def write_pack(directory, payloads, *, include_full_report=False, chunk_characters=8192, codec="gzip"):
    """Called only after load() validates the full reporting graph once."""
    check(chunk_characters in (8192, 16384), "PACK_CHUNK_SIZE_INVALID")
    roles = ["bundle", "asa_supplement"] + (["full_paired_report"] if include_full_report else [])
    selected = {role: payloads[role] for role in roles}
    check(all(canonical(json.loads(body)) == body for body in selected.values()), "PACK_COMPONENT_NOT_CANONICAL")
    packet = {"schema_version": 1, "artifact_type": "lvef_revalidation_aggregate_transport_packet_v1",
        "status": "PASS_VALIDATED_AGGREGATE_PACKET", "payloads": {role: json.loads(body) for role, body in selected.items()},
        "payload_sha256": {role: sha(body) for role, body in selected.items()},
        "payload_size_bytes": {role: len(body) for role, body in selected.items()},
        "patient_level_outputs_included": False, "poster_export_authorized": False}
    raw = canonical(packet)
    check(len(raw) <= MAX_PACKET_BYTES, "PACK_SIZE_LIMIT")
    check(codec in ("gzip", "xz"), "PACK_CODEC_INVALID")
    if codec == "xz":
        import lzma
        compressed = lzma.compress(raw, format=lzma.FORMAT_XZ, preset=6)
    else:
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    count = (len(encoded) + chunk_characters - 1) // chunk_characters
    check(1 <= count <= MAX_PACK_CHUNKS, "PACK_CHUNK_LIMIT")
    manifest = {"schema_version": 1, "artifact_type": "lvef_revalidation_aggregate_transport_manifest_v1",
        "status": "PASS_VALIDATED_AGGREGATE_PACKET", "codec": codec + "_base64", "packet_sha256": sha(raw), "packet_bytes": len(raw),
        "compressed_sha256": sha(compressed), "compressed_bytes": len(compressed), "encoded_characters": len(encoded),
        "chunk_characters": chunk_characters, "chunk_count": count,
        "chunks_sha256": [sha(encoded[i*chunk_characters:(i+1)*chunk_characters].encode("ascii")) for i in range(count)],
        "payload_sha256": packet["payload_sha256"], "payload_size_bytes": packet["payload_size_bytes"],
        "patient_level_outputs_included": False, "poster_export_authorized": False}
    check(directory.is_absolute() and not any(p.is_symlink() for p in (directory, *directory.parents)), "PACK_PATH_INVALID")
    directory.mkdir(mode=0o700)
    exclusive_bytes(directory / "packet.compressed", compressed)
    exclusive_bytes(directory / "packet.manifest.json", canonical(manifest))
    return manifest


def read_pack(directory, expected_manifest_sha256):
    body = packet_private_bytes(directory / "packet.manifest.json", 128 * 1024)
    check(sha(body) == expected_manifest_sha256, "PACK_MANIFEST_HASH_CHANGED")
    manifest = json.loads(body)
    validate_pack_manifest(manifest)
    compressed = packet_private_bytes(directory / "packet.compressed", MAX_PACKET_BYTES)
    check(len(compressed) == manifest["compressed_bytes"] and sha(compressed) == manifest["compressed_sha256"], "PACK_GZIP_CHANGED")
    encoded = base64.b64encode(compressed).decode("ascii")
    check(len(encoded) == manifest["encoded_characters"], "PACK_ENCODED_LENGTH_CHANGED")
    return manifest, encoded


def validate_pack_manifest(manifest):
    check(set(manifest) == {"schema_version", "artifact_type", "status", "codec", "packet_sha256", "packet_bytes", "compressed_sha256",
          "compressed_bytes", "encoded_characters", "chunk_characters", "chunk_count", "chunks_sha256", "payload_sha256", "payload_size_bytes",
          "patient_level_outputs_included", "poster_export_authorized"}
          and manifest["schema_version"] == 1 and manifest["artifact_type"] == "lvef_revalidation_aggregate_transport_manifest_v1"
          and manifest["status"] == "PASS_VALIDATED_AGGREGATE_PACKET" and manifest["codec"] in ("gzip_base64", "xz_base64")
          and manifest["patient_level_outputs_included"] is False and manifest["poster_export_authorized"] is False,
          "PACK_MANIFEST_SCHEMA_INVALID")
    check(type(manifest["packet_bytes"]) is int and 0 < manifest["packet_bytes"] <= MAX_PACKET_BYTES
          and type(manifest["compressed_bytes"]) is int and 0 < manifest["compressed_bytes"] <= MAX_PACKET_BYTES
          and manifest["chunk_characters"] in (8192, 16384) and type(manifest["chunk_count"]) is int
          and 1 <= manifest["chunk_count"] <= MAX_PACK_CHUNKS
          and manifest["chunk_count"] == (manifest["encoded_characters"] + manifest["chunk_characters"] - 1) // manifest["chunk_characters"]
          and len(manifest["chunks_sha256"]) == manifest["chunk_count"], "PACK_MANIFEST_SIZE_INVALID")
    roles = set(manifest["payload_sha256"])
    check(roles in ({"bundle", "asa_supplement"}, {"bundle", "asa_supplement", "full_paired_report"})
          and set(manifest["payload_size_bytes"]) == roles, "PACK_ROLE_SET_INVALID")
    hashes = [manifest["packet_sha256"], manifest["compressed_sha256"], *manifest["chunks_sha256"], *manifest["payload_sha256"].values()]
    check(all(isinstance(x, str) and __import__("re").fullmatch(r"[0-9a-f]{64}", x) for x in hashes), "PACK_HASH_INVALID")


def pack_chunks(manifest, encoded, start, count):
    check(type(start) is int and type(count) is int and 0 <= start < manifest["chunk_count"]
          and 1 <= count <= 8 and start + count <= manifest["chunk_count"], "PACK_CHUNK_RANGE_INVALID")
    result = []
    for index in range(start, start + count):
        text = encoded[index*manifest["chunk_characters"]:(index+1)*manifest["chunk_characters"]]
        check(sha(text.encode("ascii")) == manifest["chunks_sha256"][index], "PACK_CHUNK_CHANGED")
        result.append({"marker": PACK_MARKER, "index": index, "chunks": manifest["chunk_count"], "packet_sha256": manifest["packet_sha256"],
            "compressed_sha256": manifest["compressed_sha256"], "chunk_sha256": manifest["chunks_sha256"][index], "compressed_base64": text})
    return result


def decode_pack(manifest, chunks, expected_manifest_sha256):
    """Local receiver: exact manifest/chunk/gzip/packet/component hash replay."""
    check(sha(canonical(manifest)) == expected_manifest_sha256, "PACK_MANIFEST_HASH_CHANGED")
    validate_pack_manifest(manifest)
    check(len(chunks) == manifest["chunk_count"] and {c["index"] for c in chunks} == set(range(manifest["chunk_count"])), "PACK_MISSING_OR_DUPLICATE_CHUNKS")
    parts = []
    for row in sorted(chunks, key=lambda c: c["index"]):
        check(set(row) == {"marker", "index", "chunks", "packet_sha256", "compressed_sha256", "chunk_sha256", "compressed_base64"}
              and row["marker"] == PACK_MARKER and row["chunks"] == manifest["chunk_count"]
              and row["packet_sha256"] == manifest["packet_sha256"] and row["compressed_sha256"] == manifest["compressed_sha256"]
              and row["chunk_sha256"] == manifest["chunks_sha256"][row["index"]]
              and sha(row["compressed_base64"].encode("ascii")) == row["chunk_sha256"], "PACK_CHUNK_CHANGED")
        parts.append(row["compressed_base64"])
    encoded = "".join(parts)
    check(len(encoded) == manifest["encoded_characters"], "PACK_ENCODED_LENGTH_CHANGED")
    compressed = base64.b64decode(encoded, validate=True)
    check(len(compressed) == manifest["compressed_bytes"] and sha(compressed) == manifest["compressed_sha256"], "PACK_GZIP_CHANGED")
    if manifest["codec"] == "xz_base64":
        import lzma
        stream = lzma.LZMAFile(io.BytesIO(compressed))
    else:
        stream = gzip.GzipFile(fileobj=io.BytesIO(compressed))
    with stream:
        body = stream.read(manifest["packet_bytes"] + 1)
    check(len(body) == manifest["packet_bytes"] and sha(body) == manifest["packet_sha256"], "PACK_PACKET_CHANGED")
    packet = json.loads(body)
    check(canonical(packet) == body and set(packet) == {"schema_version", "artifact_type", "status", "payloads", "payload_sha256", "payload_size_bytes", "patient_level_outputs_included", "poster_export_authorized"}
          and packet["schema_version"] == 1 and packet["artifact_type"] == "lvef_revalidation_aggregate_transport_packet_v1"
          and packet["status"] == manifest["status"] and packet["patient_level_outputs_included"] is False
          and packet["poster_export_authorized"] is False and packet["payload_sha256"] == manifest["payload_sha256"]
          and packet["payload_size_bytes"] == manifest["payload_size_bytes"] and set(packet["payloads"]) == set(manifest["payload_sha256"]), "PACK_PACKET_SCHEMA_INVALID")
    result = {role: canonical(value) for role, value in packet["payloads"].items()}
    check(all(sha(body) == manifest["payload_sha256"][role] and len(body) == manifest["payload_size_bytes"][role]
              for role, body in result.items()), "PACK_COMPONENT_CHANGED")
    return result


def check(ok, code):
    if not ok:
        raise ValueError(code)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def interval(value, *, model=False):
    common = {"interval", "valid_replicates", "undefined_replicates", "undefined_frequency", "replicates"}
    defined = common | ({"estimate", "interval_method", "supports_paired_superiority_claim"} if model else
        {"effect", "p_value", "interval_method", "p_value_method"})
    undefined = common | {"status", "effect", "p_value"}
    check(set(value) == defined or not model and set(value) == undefined, "INTERVAL_SCHEMA_INVALID")
    renderer._interval(value, model=model)
    check(math.isclose(value["undefined_frequency"], value["undefined_replicates"] / 10000, abs_tol=1e-12), "UNDEFINED_FREQUENCY_INVALID")
    if model:
        check(value["interval_method"] == "percentile_95" and value["supports_paired_superiority_claim"] is False, "MODEL_INTERVAL_ROLE_INVALID")
    elif "status" in value:
        check(value["status"] == "OBSERVED_METRIC_UNDEFINED" and value["effect"] is value["interval"] is value["p_value"] is None,
              "UNDEFINED_INTERVAL_INVALID")
    else:
        check(value["interval_method"] == "percentile_95" and value["p_value_method"] == "null_centered_two_sided_plus_one", "PAIRED_INTERVAL_METHOD_INVALID")


def contrast_set(value):
    check(set(value) == CONTRASTS, "CONTRAST_SET_INVALID")
    for item in value.values():
        interval(item)


def scalar_models(value):
    check(set(value) == set(engine.MODALITIES) and all(type(x) in (int, float) and math.isfinite(x) for x in value.values()), "MODEL_SCALARS_INVALID")


def simple_bootstrap(value):
    check(set(value) == {"contrasts", "one_class_replicates", "one_class_frequency", "observed", "replicates", "shared_subject_multiplicities"}
          and value["replicates"] == 10000 and value["shared_subject_multiplicities"] is True, "BOOTSTRAP_SCHEMA_INVALID")
    contrast_set(value["contrasts"]); scalar_models(value["observed"])
    check(type(value["one_class_replicates"]) is int and 0 <= value["one_class_replicates"] <= 10000
          and value["one_class_frequency"] == value["one_class_replicates"] / 10000, "ONE_CLASS_COUNTS_INVALID")


def collection(value, metrics, *, binary=False):
    keys = {"contrasts", "model_metrics", "replicates", "shared_subject_multiplicities", "effect_orientation", "metric_directions", "scope"}
    if binary:
        keys |= {"one_class_replicates", "one_class_frequency"}
    check(set(value) == keys and value["replicates"] == 10000 and value["shared_subject_multiplicities"] is True
          and value["effect_orientation"] == "named_left_modality_minus_named_right_modality"
          and value["scope"] == "secondary_effects_and_intervals_no_additional_superiority_claim", "METRIC_COLLECTION_SCHEMA_INVALID")
    check(set(value["contrasts"]) == CONTRASTS and set(value["model_metrics"]) == set(engine.MODALITIES)
          and value["metric_directions"] == {k: inference.metric_orientation(k) for k in metrics}, "METRIC_COLLECTION_MEMBERS_INVALID")
    for group in value["contrasts"].values():
        check(set(group) == metrics, "PAIRED_METRIC_SET_INVALID")
        for item in group.values():
            interval(item)
    for group in value["model_metrics"].values():
        check(set(group) == metrics, "MODEL_METRIC_SET_INVALID")
        for item in group.values():
            interval(item, model=True)
    if binary:
        check(type(value["one_class_replicates"]) is int and 0 <= value["one_class_replicates"] <= 10000
              and value["one_class_frequency"] == value["one_class_replicates"] / 10000, "ONE_CLASS_COUNTS_INVALID")


def validate_report(report, candidate):
    check(set(report) == {"status", "evaluation_receipt_sha256", "analysis_lock_sha256", "spec_sha256", "conditions",
          "patient_level_outputs_exported", "public_export_approved"}
          and report["status"] == "PASS_PRIVATE_PAIRED_REPORT" and report["patient_level_outputs_exported"] is False
          and report["public_export_approved"] is False and report["spec_sha256"] == candidate["spec_sha256"]
          and set(report["conditions"]) == set(engine.EVALUATION_CONDITIONS), "PAIRED_REPORT_SCHEMA_INVALID")
    panel = candidate["strict_panel"]
    base_metrics = set(renderer.METRICS)
    lvef_metrics = base_metrics | {"tolerance_" + str(x) for x in (4., 5., 8.)} | {
        "band_" + band + "_" + side + "_mae" for band in ("35_45", "37_43") for side in ("inside", "outside")}
    binary_base = {"prevalence", "auroc", "average_precision", "sensitivity", "specificity", "ppv", "npv", "f1", "balanced_accuracy"}
    binary_metrics = binary_base | {"calibrated_brier_score", "calibration_intercept", "calibration_slope"} | {
        prefix + key for prefix in ("fixed_0_5_", "coherence_") for key in binary_base}
    summaries = {}
    for condition in engine.EVALUATION_CONDITIONS:
        value = report["conditions"][condition]
        check(set(value) == {"primary_inference", "secondary_intervals", "panel_summary"}
              and value["panel_summary"] == candidate["panel_summary"][condition], "REPORT_CONDITION_SCHEMA_INVALID")
        primary = value["primary_inference"]
        check(set(primary) == {"replicates", "seed", "lvef_mae", "strict_panel", "condition", "endpoint", "core_multiplicity",
              "secondary_logistic_auroc", "secondary_binary_holm", "binary_family_role", "inference_scope"}
              and primary["condition"] == condition and primary["endpoint"] == "lvef_lt_40" and primary["replicates"] == 10000
              and primary["seed"] == engine.SEED and primary["binary_family_role"] == "secondary_not_core_global_fwer"
              and primary["inference_scope"] == "conditional_on_fixed_selected_models", "PRIMARY_INFERENCE_SCHEMA_INVALID")
        simple_bootstrap(primary["lvef_mae"]); simple_bootstrap(primary["secondary_logistic_auroc"])
        macro = primary["strict_panel"]
        check(set(macro) == {"task_native_mae_contrasts", "macro_normalized_mae", "macro_contrasts", "locked_task_count", "roster_subjects",
              "effect_orientation", "metric_direction", "shared_subject_multiplicities", "undefined_task_invalidates_macro"}
              and macro["locked_task_count"] == len(panel) and set(macro["task_native_mae_contrasts"]) == set(panel)
              and macro["roster_subjects"] == candidate["flow"]["imaging_split_counts"]["test"]
              and macro["shared_subject_multiplicities"] is True and macro["undefined_task_invalidates_macro"] is True
              and macro["effect_orientation"] == "named_left_modality_minus_named_right_modality"
              and macro["metric_direction"] == "lower_is_better", "STRICT_PANEL_BOOTSTRAP_INVALID")
        scalar_models(macro["macro_normalized_mae"]); contrast_set(macro["macro_contrasts"])
        for item in macro["task_native_mae_contrasts"].values():
            contrast_set(item)
        for modality in engine.MODALITIES:
            check(math.isclose(macro["macro_normalized_mae"][modality], value["panel_summary"][modality]["macro_normalized_mae"], abs_tol=1e-12), "MACRO_ESTIMATE_BINDING_INVALID")
        if condition == "primary":
            p = {}
            for i, contrast in enumerate(ORDERED_CONTRASTS[:2]):
                p[inference.CORE_CLAIMS[i]] = primary["lvef_mae"]["contrasts"][contrast]["p_value"]
                p[inference.CORE_CLAIMS[i + 2]] = macro["macro_contrasts"][contrast]["p_value"]
            expected = inference.core_holm(p, strict_panel=panel, strict_panel_locked=True)
            check(primary["core_multiplicity"] == expected and candidate["core_holm"] == expected["adjusted_p_values"], "CORE_HOLM_REPLAY_INVALID")
            binary_p = {k: primary["secondary_logistic_auroc"]["contrasts"][k]["p_value"] for k in ORDERED_CONTRASTS[:2]}
            check(primary["secondary_binary_holm"] == inference.holm(binary_p), "SECONDARY_BINARY_HOLM_REPLAY_INVALID")
        else:
            check(primary["core_multiplicity"] == {"status": "SECONDARY_NO_CORE_CLAIM"}
                  and primary["secondary_binary_holm"] == {"status": "DESCRIPTIVE_NO_ADDITIONAL_HOLM_FAMILY"}, "SENSITIVITY_MULTIPLICITY_INVALID")
        secondary = value["secondary_intervals"]
        check(set(secondary) == {"replicates", "seed", "condition", "targets", "shared_panel_draws_across_tasks", "fixed_models"}
              and secondary["replicates"] == 10000 and secondary["seed"] == engine.SEED and secondary["condition"] == condition
              and secondary["shared_panel_draws_across_tasks"] is True and secondary["fixed_models"] is True
              and set(secondary["targets"]) == {"lvef", *panel}, "SECONDARY_INTERVALS_SCHEMA_INVALID")
        for target, target_value in secondary["targets"].items():
            check(set(target_value) == ({"continuous", "binary"} if target == "lvef" else {"continuous"}), "SECONDARY_TARGET_SCHEMA_INVALID")
            collection(target_value["continuous"], lvef_metrics if target == "lvef" else base_metrics)
            if target == "lvef":
                check(set(target_value["binary"]) == set(engine.ENDPOINTS), "SECONDARY_ENDPOINT_SET_INVALID")
                for endpoint, item in target_value["binary"].items():
                    if item == {"status": "UNSUPPORTED_CLASS_COUNTS"}:
                        check(endpoint != "lvef_lt_40", "PRIMARY_BINARY_UNSUPPORTED")
                    else:
                        collection(item, binary_metrics, binary=True)
        summaries[condition] = {"lvef_mae": primary["lvef_mae"], "strict_panel_macro": {
            key: macro[key] for key in ("macro_normalized_mae", "macro_contrasts", "locked_task_count", "roster_subjects",
                "shared_subject_multiplicities", "undefined_task_invalidates_macro")},
            "core_multiplicity": primary["core_multiplicity"], "secondary_logistic_lt40": primary["secondary_logistic_auroc"],
            "secondary_binary_holm": primary["secondary_binary_holm"], "panel_summary": value["panel_summary"]}
    return summaries


def load(root, expected_bundle_sha256):
    bundle_body = authority.private_bytes(root / "aggregate_bundle.restricted.json")
    check(sha(bundle_body) == expected_bundle_sha256, "BUNDLE_HASH_CHANGED")
    bundle = authority.decode(bundle_body)
    candidate = renderer.validate_bundle(bundle)
    safety_body = authority.private_bytes(root / "aggregate_safety.restricted.json")
    check(sha(safety_body) == bundle["aggregate_safety"]["safety_receipt_sha256"], "SAFETY_HASH_CHANGED")
    safety = authority.decode(safety_body)
    check(set(safety) == {"status", "candidate_sha256", "analysis_lock_sha256", "input_sha256", "evaluation_receipt_sha256", "report_sha256",
          "checks", "validator_sha256", "patient_level_outputs_exported", "public_export_approved"}
          and safety["status"] == "PASS_REVALIDATION_AGGREGATE_SAFETY" and safety["candidate_sha256"] == bundle["candidate_sha256"]
          and safety["report_sha256"] == candidate["report_sha256"]
          and safety["patient_level_outputs_exported"] is False and safety["public_export_approved"] is False,
          "SAFETY_BINDING_INVALID")
    report_body = authority.private_bytes(root / "paired_report.restricted.json")
    check(sha(report_body) == candidate["report_sha256"], "PAIRED_REPORT_HASH_CHANGED")
    report = authority.decode(report_body)
    evaluation_body = authority.private_bytes(root / "test_evaluation.restricted.json")
    check(sha(evaluation_body) == safety["evaluation_receipt_sha256"] == report["evaluation_receipt_sha256"], "EVALUATION_RECEIPT_HASH_CHANGED")
    evaluation = authority.decode(evaluation_body)
    check(evaluation["status"] == "PASS_LOCKED_TEST_EVALUATION" and evaluation["test_loader_invocations"] == 1
          and evaluation["predictions_sha256"] == candidate["evaluation_sha256"]
          and evaluation["frozen_sha256"] == candidate["frozen_sha256"]
          and evaluation["spec_sha256"] == candidate["spec_sha256"] == report["spec_sha256"]
          and evaluation["analysis_lock_sha256"] == safety["analysis_lock_sha256"] == report["analysis_lock_sha256"]
          and evaluation["input_sha256"] == safety["input_sha256"] == candidate["input_audit_sha256"], "EVALUATION_BINDING_INVALID")
    for body, name in ((bundle_body, "aggregate_bundle.json"), (report_body, "paired_report.json"), (safety_body, "aggregate_safety.json")):
        check(authority.canonical(authority.decode(body)) == body, "NONCANONICAL_AGGREGATE_BODY")
        git_safety._assert_global_json_safety(body, policy)
        git_safety._assert_high_confidence_text_safety(body, name)
        renderer._no_rows(authority.decode(body))
    summaries = validate_report(report, candidate)
    supplement = {"schema_version": 1, "artifact_type": "lvef_revalidation_asa_aggregate_supplement_v1",
        "status": "PASS_VALIDATED_ASA_AGGREGATE_SUPPLEMENT", "strict_panel": candidate["strict_panel"],
        "source_sha256": {"bundle": sha(bundle_body), "paired_report": sha(report_body), "aggregate_safety": sha(safety_body),
            "evaluation_receipt": sha(evaluation_body), "spec": candidate["spec_sha256"], "frozen_models": candidate["frozen_sha256"]},
        "conditions": summaries, "secondary_intervals_complete_in_bound_paired_report": True,
        "model_refitted": False, "bootstrap_recomputed": False, "patient_level_outputs": False, "poster_export_authorized": False}
    supplement_body = authority.canonical(supplement)
    git_safety._assert_global_json_safety(supplement_body, policy)
    git_safety._assert_high_confidence_text_safety(supplement_body, "asa_supplement.json")
    return {"asa_supplement": supplement_body, "full_paired_report": report_body, "bundle": bundle_body, "aggregate_safety": safety_body}


def initialize(repository):
    global authority, engine, inference, renderer, git_safety, policy, CONTRASTS, ORDERED_CONTRASTS
    sys.path.insert(0, str(repository / "scripts"))
    import lvef_revalidation_authority as authority
    import lvef_revalidation_analysis as engine
    import lvef_revalidation_inference as inference
    import render_lvef_revalidation_results as renderer
    import check_lvef_git_export_safety as git_safety
    from lvef_multitask_analysis_modes import load_policy
    policy, _ = load_policy(repository / "configs/lvef_multitask_safe_export_policy.yaml")
    ORDERED_CONTRASTS = [f"{left}_minus_{right}" for left, right in inference.CONTRASTS]
    CONTRASTS = set(ORDERED_CONTRASTS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--analysis-root", type=Path)
    parser.add_argument("--expected-bundle-sha256")
    parser.add_argument("--payload", choices=("asa_supplement", "full_paired_report", "bundle", "aggregate_safety"), default="asa_supplement")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--output", type=Path)
    mode.add_argument("--chunk", type=int)
    mode.add_argument("--pack-output", type=Path)
    mode.add_argument("--pack-read", type=Path)
    parser.add_argument("--expected-pack-manifest-sha256")
    parser.add_argument("--pack-codec", choices=("gzip", "xz"), default="gzip")
    parser.add_argument("--chunk-characters", type=int, choices=(8192, 16384), default=8192)
    parser.add_argument("--include-full-report", action="store_true")
    parser.add_argument("--chunk-start", type=int, default=0)
    parser.add_argument("--chunk-count", type=int, default=1)
    args = parser.parse_args()
    try:
        if args.pack_read:
            check(args.expected_pack_manifest_sha256 is not None, "PACK_MANIFEST_HASH_REQUIRED")
            manifest, encoded = read_pack(args.pack_read, args.expected_pack_manifest_sha256)
            for row in pack_chunks(manifest, encoded, args.chunk_start, args.chunk_count):
                print(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False))
            return 0
        check(args.repository is not None and args.analysis_root is not None and args.expected_bundle_sha256 is not None,
              "BOUND_ANALYSIS_ARGUMENTS_REQUIRED")
        initialize(args.repository)
        payloads = load(args.analysis_root, args.expected_bundle_sha256)
        if args.pack_output:
            import time
            started = time.monotonic()
            manifest = write_pack(args.pack_output, payloads, include_full_report=args.include_full_report,
                chunk_characters=args.chunk_characters, codec=args.pack_codec)
            print(json.dumps({"status": "PASS_VALIDATED_AGGREGATE_PACK_CREATED", "manifest": manifest,
                "manifest_sha256": sha(canonical(manifest)), "pack_elapsed_seconds": time.monotonic() - started},
                sort_keys=True, allow_nan=False))
            return 0
        body = payloads[args.payload]
        encoded = base64.b64encode(gzip.compress(body, mtime=0)).decode("ascii")
        chunk_size = 2048
        chunks = (len(encoded) + chunk_size - 1) // chunk_size
        metadata = {"status": "PASS_VALIDATED_AGGREGATE_TRANSPORT", "payload": args.payload, "sha256": sha(body), "bytes": len(body),
            "gzip_base64_bytes": len(encoded), "chunk_characters": chunk_size, "chunks": chunks,
            "all_payload_sizes": {key: {"bytes": len(value), "sha256": sha(value), "gzip_bytes": len(gzip.compress(value, mtime=0))} for key, value in payloads.items()},
            "source_patient_rows_read": False, "source_files_modified": False, "poster_export_authorized": False}
        if args.output:
            with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
                stream.write(body); stream.flush(); os.fsync(stream.fileno())
            check(args.output.read_bytes() == body, "OUTPUT_READBACK_FAILED")
        elif args.chunk is not None:
            check(0 <= args.chunk < chunks, "CHUNK_INDEX_INVALID")
            text = encoded[args.chunk * chunk_size:(args.chunk + 1) * chunk_size]
            metadata = {"status": "PASS_VALIDATED_AGGREGATE_CHUNK", "payload": args.payload, "sha256": sha(body), "bytes": len(body),
                "index": args.chunk, "chunks": chunks, "chunk_sha256": sha(text.encode("ascii")), "gzip_base64": text}
        print(json.dumps(metadata, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError else getattr(exc, "code", "AGGREGATE_TRANSPORT_BLOCKED")
        if not isinstance(code, str) or not __import__("re").fullmatch(r"[A-Z][A-Z0-9_]{3,100}", code):
            code = "AGGREGATE_TRANSPORT_BLOCKED"
        print(json.dumps({"status": "BLOCKED_AGGREGATE_TRANSPORT", "code": code}))
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
