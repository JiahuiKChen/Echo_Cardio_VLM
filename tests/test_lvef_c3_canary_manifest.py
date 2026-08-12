from __future__ import annotations

# SYNTHETIC_CANARY_CONTROL_PLANE_ONLY: no cloud, SCC, qsub, DICOM, or GPU.
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_canary_manifest as canary


COMMIT = "a" * 40
SOURCE_SHA = "b" * 64
CONFIG_HASHES = {
    "execution_state": "c" * 64,
    "production_contract": "d" * 64,
}


def _assert_error(code: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except canary.CanaryManifestError as exc:
        assert str(exc) == code, (str(exc), code)
    else:
        raise AssertionError(f"expected CanaryManifestError {code}")


def _candidate(
    index: int,
    *,
    object_count: int | None = None,
    byte_total: int | None = None,
    split: str = "train",
    known_no_cine: bool = False,
    prior_smoke: bool = False,
) -> dict[str, Any]:
    return {
        "study_id": str(200000 + index),
        "subject_id": str(100000 + index),
        "split": split,
        "expected_object_count": object_count if object_count is not None else index,
        "expected_byte_total": byte_total if byte_total is not None else index * 100,
        "known_no_cine": known_no_cine,
        "prior_reconstruction_smoke": prior_smoke,
    }


def _source_object(selected: canary.SelectedStudy, ordinal: int = 1) -> dict[str, Any]:
    path = (
        f"files/p{int(selected.subject_id) // 1_000_000:02d}/p{selected.subject_id}/"
        f"s{selected.study_id}/cine_{ordinal:03d}.dcm"
    )
    return {
        "subject_id": selected.subject_id,
        "study_id": selected.study_id,
        "split": "train",
        "source_object_key": hashlib.sha256(
            f"{canary.SOURCE_RELEASE}\0{path}".encode("utf-8")
        ).hexdigest(),
        "source_relative_path": path,
        "size_bytes": 100,
        "generation": str(9000 + ordinal),
        "md5_base64": base64.b64encode(bytes([ordinal % 251]) * 16).decode(),
        "crc32c_base64": base64.b64encode(bytes([ordinal % 251]) * 4).decode(),
    }


def _fixture() -> tuple[list[canary.SelectedStudy], list[dict[str, Any]], dict[str, Any]]:
    candidates = [_candidate(index, object_count=1, byte_total=100) for index in range(1, 6)]
    selected = canary.select_exact_five(candidates)
    objects = [_source_object(item) for item in selected]
    sealed = canary.build_sealed_manifest(
        selected_studies=selected,
        source_objects=objects,
        source_authority_commit=COMMIT,
        source_manifest_sha256=SOURCE_SHA,
        source_configuration_hashes=CONFIG_HASHES,
    )
    return selected, objects, sealed


def test_exact_five_manifest_schema_and_self_seal_pass() -> None:
    _, _, sealed = _fixture()
    validated = canary.validate_manifest(
        sealed,
        expected_manifest_sha256=sealed["manifest_sha256"],
        expected_source_authority_commit=COMMIT,
    )
    body = validated["manifest"]
    assert set(validated) == canary.ENVELOPE_KEYS
    assert set(body) == canary.MANIFEST_KEYS
    assert body["study_count"] == body["subject_count"] == 5
    assert body["split"] == "train"
    assert body["complete_object_membership"] is True
    assert body["expected_object_count"] == 5
    assert body["expected_byte_total"] == 500
    assert [item["selection_stratum"] for item in body["studies"]] == list(
        canary.SELECTION_STRATA
    )
    assert len({item["study_id"] for item in body["studies"]}) == 5
    assert len({item["subject_id"] for item in body["studies"]}) == 5
    assert body["source_configuration_hashes"] == [
        {"logical_name": name, "sha256": digest}
        for name, digest in sorted(CONFIG_HASHES.items())
    ]


def test_manifest_rejects_four_six_nontrain_and_duplicate_identities() -> None:
    _, _, sealed = _fixture()
    body = sealed["manifest"]
    four = copy.deepcopy(body)
    four["studies"].pop()
    _assert_error("CANARY_EXACT_FIVE_COUNT_INVALID", lambda: canary.seal_manifest(four))

    six = copy.deepcopy(body)
    six["studies"].append(copy.deepcopy(six["studies"][-1]))
    _assert_error("CANARY_EXACT_FIVE_COUNT_INVALID", lambda: canary.seal_manifest(six))

    nontrain = copy.deepcopy(body)
    nontrain["studies"][2]["split"] = "val"
    _assert_error("CANARY_SPLIT_NOT_TRAIN", lambda: canary.seal_manifest(nontrain))
    nontrain["studies"][2]["split"] = "test"
    _assert_error("CANARY_SPLIT_NOT_TRAIN", lambda: canary.seal_manifest(nontrain))

    duplicate_study = copy.deepcopy(body)
    duplicate_study["studies"][1]["study_id"] = duplicate_study["studies"][0]["study_id"]
    _assert_error("CANARY_STUDY_DUPLICATE", lambda: canary.seal_manifest(duplicate_study))
    duplicate_subject = copy.deepcopy(body)
    duplicate_subject["studies"][1]["subject_id"] = duplicate_subject["studies"][0]["subject_id"]
    _assert_error("CANARY_SUBJECT_DUPLICATE", lambda: canary.seal_manifest(duplicate_subject))


def test_manifest_rejects_unknown_prohibited_duplicate_json_and_tampering() -> None:
    _, _, sealed = _fixture()
    unknown = copy.deepcopy(sealed["manifest"])
    unknown["unknown_control"] = False
    _assert_error(
        "CANARY_MANIFEST_BODY_SCHEMA_NOT_CLOSED",
        lambda: canary.seal_manifest(unknown),
    )
    forbidden = copy.deepcopy(sealed["manifest"])
    forbidden["target_label"] = None
    _assert_error("PROHIBITED_FIELD_PRESENT", lambda: canary.seal_manifest(forbidden))

    _assert_error(
        "JSON_DUPLICATE_KEY",
        lambda: canary.parse_manifest_bytes(
            b'{"schema_name":"one","schema_name":"two"}\n'
        ),
    )
    altered = copy.deepcopy(sealed)
    altered["manifest"]["source_manifest_sha256"] = "e" * 64
    _assert_error("CANARY_MANIFEST_SEAL_MISMATCH", lambda: canary.validate_manifest(altered))


def test_deterministic_source_only_quintile_selection_and_exclusions() -> None:
    eligible = [_candidate(index) for index in range(1, 10)]
    excluded = [
        _candidate(20, object_count=1, split="val"),
        _candidate(21, object_count=2, split="test"),
        _candidate(22, object_count=3, known_no_cine=True),
        _candidate(23, object_count=4, prior_smoke=True),
    ]
    rows = eligible + excluded
    first = canary.select_exact_five(rows)
    shuffled = list(rows)
    random.Random(20260812).shuffle(shuffled)
    second = canary.select_exact_five(shuffled)
    assert first == second
    assert [item.expected_object_count for item in first] == [1, 3, 5, 7, 9]
    assert [item.selection_stratum for item in first] == list(canary.SELECTION_STRATA)
    assert all(item.split == "train" for item in first)
    assert all(not item.known_no_cine for item in first)
    assert all(not item.prior_reconstruction_smoke for item in first)


def test_selection_uses_frozen_numeric_tiebreak_and_one_study_per_subject() -> None:
    rows = [_candidate(index, object_count=10, byte_total=100) for index in range(1, 7)]
    # Same subject as candidate 1, but numerically later study: candidate 1 is
    # the frozen representative regardless of input order.
    alternate = _candidate(99, object_count=1, byte_total=1)
    alternate["subject_id"] = rows[0]["subject_id"]
    selected = canary.select_exact_five([alternate, *reversed(rows)])
    assert alternate["study_id"] not in {item.study_id for item in selected}
    assert len({item.subject_id for item in selected}) == 5


def test_selection_rejects_duplicate_studies_and_hard_ceiling_breaches() -> None:
    duplicate = [_candidate(index) for index in range(1, 6)]
    duplicate.append(copy.deepcopy(duplicate[0]))
    _assert_error(
        "CANDIDATE_STUDY_DUPLICATE", lambda: canary.select_exact_five(duplicate)
    )
    object_breach = [
        _candidate(index, object_count=151, byte_total=100) for index in range(1, 6)
    ]
    _assert_error(
        "CANARY_OBJECT_CEILING_EXCEEDED",
        lambda: canary.select_exact_five(object_breach),
    )
    byte_breach = [
        _candidate(index, object_count=1, byte_total=1_000_000_001)
        for index in range(1, 6)
    ]
    _assert_error(
        "CANARY_BYTE_CEILING_EXCEEDED",
        lambda: canary.select_exact_five(byte_breach),
    )


def test_candidate_csv_rejects_duplicate_unknown_and_prohibited_columns() -> None:
    header = ",".join(canary.CANDIDATE_COLUMNS)
    row = "200001,100001,train,1,100,false,false"
    parsed = canary.parse_candidate_csv_bytes(f"{header}\n{row}\n".encode())
    assert parsed == [_candidate(1, object_count=1, byte_total=100)]

    duplicate_header = header.replace("subject_id", "study_id")
    _assert_error(
        "CSV_DUPLICATE_COLUMN",
        lambda: canary.parse_candidate_csv_bytes(
            f"{duplicate_header}\n{row}\n".encode()
        ),
    )
    unknown_header = header.replace("subject_id", "source_rank")
    _assert_error(
        "CSV_SCHEMA_NOT_CLOSED",
        lambda: canary.parse_candidate_csv_bytes(
            f"{unknown_header}\n{row}\n".encode()
        ),
    )
    prohibited_header = header.replace("subject_id", "endpoint_value")
    _assert_error(
        "PROHIBITED_FIELD_PRESENT",
        lambda: canary.parse_candidate_csv_bytes(
            f"{prohibited_header}\n{row}\n".encode()
        ),
    )


def test_build_rejects_undeclared_duplicate_and_incomplete_object_membership() -> None:
    selected, objects, _ = _fixture()
    undeclared = copy.deepcopy(objects)
    undeclared[0]["study_id"] = "299999"
    _assert_error(
        "CANARY_UNDECLARED_STUDY_OBJECT",
        lambda: canary.build_sealed_manifest(
            selected_studies=selected,
            source_objects=undeclared,
            source_authority_commit=COMMIT,
            source_manifest_sha256=SOURCE_SHA,
            source_configuration_hashes=CONFIG_HASHES,
        ),
    )
    duplicate = [*objects, copy.deepcopy(objects[0])]
    _assert_error(
        "CANARY_OBJECT_KEY_DUPLICATE",
        lambda: canary.build_sealed_manifest(
            selected_studies=selected,
            source_objects=duplicate,
            source_authority_commit=COMMIT,
            source_manifest_sha256=SOURCE_SHA,
            source_configuration_hashes=CONFIG_HASHES,
        ),
    )
    incomplete = objects[:-1]
    _assert_error(
        "CANARY_STUDY_OBJECT_COUNT_MISMATCH",
        lambda: canary.build_sealed_manifest(
            selected_studies=selected,
            source_objects=incomplete,
            source_authority_commit=COMMIT,
            source_manifest_sha256=SOURCE_SHA,
            source_configuration_hashes=CONFIG_HASHES,
        ),
    )


def test_private_file_load_no_follow_and_write_no_clobber() -> None:
    _, _, sealed = _fixture()
    with tempfile.TemporaryDirectory() as raw:
        private = Path(raw).resolve() / "private"
        private.mkdir(mode=0o700)
        private.chmod(0o700)
        manifest_path = private / "canary.restricted.json"
        file_sha, embedded_sha = canary.write_private_manifest_no_clobber(
            manifest_path, sealed
        )
        assert (manifest_path.stat().st_mode & 0o7777) == 0o600
        loaded = canary.load_and_validate_manifest(
            manifest_path,
            expected_file_sha256=file_sha,
            expected_manifest_sha256=embedded_sha,
            expected_source_authority_commit=COMMIT,
        )
        assert loaded == sealed
        _assert_error(
            "CANARY_OUTPUT_ALREADY_EXISTS",
            lambda: canary.write_private_manifest_no_clobber(manifest_path, sealed),
        )
        link = private / "link.restricted.json"
        os.symlink(manifest_path, link)
        _assert_error(
            "CANARY_MANIFEST_SYMLINK_FORBIDDEN",
            lambda: canary.load_and_validate_manifest(link),
        )
        real_parent = private / "real-parent"
        real_parent.mkdir(mode=0o700)
        nested = real_parent / "nested"
        nested.mkdir(mode=0o700)
        nested_manifest = nested / "canary.restricted.json"
        nested_manifest.write_bytes(canary.serialize_manifest(sealed))
        nested_manifest.chmod(0o600)
        linked_parent = private / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        _assert_error(
            "CANARY_MANIFEST_ANCESTOR_INVALID",
            lambda: canary.load_and_validate_manifest(
                linked_parent / "nested" / "canary.restricted.json"
            ),
        )
        manifest_path.chmod(0o640)
        _assert_error(
            "CANARY_MANIFEST_MODE_INVALID",
            lambda: canary.load_and_validate_manifest(manifest_path),
        )


def test_tracked_json_schema_is_closed_and_matches_runtime_constants() -> None:
    schema_path = ROOT / "configs" / "lvef_c3_canary_manifest_schema_v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_name"]["const"] == canary.SCHEMA_NAME
    assert schema["properties"]["schema_version"]["const"] == canary.SCHEMA_VERSION
    body = schema["properties"]["manifest"]
    assert body["additionalProperties"] is False
    assert body["properties"]["study_count"]["const"] == canary.EXACT_STUDIES
    assert body["properties"]["subject_count"]["const"] == canary.EXACT_SUBJECTS
    assert body["properties"]["expected_object_count"]["maximum"] == canary.MAXIMUM_OBJECTS
    assert body["properties"]["expected_byte_total"]["maximum"] == canary.MAXIMUM_EXPECTED_BYTES
    assert schema["$defs"]["study"]["additionalProperties"] is False
    assert schema["$defs"]["sourceObject"]["additionalProperties"] is False
