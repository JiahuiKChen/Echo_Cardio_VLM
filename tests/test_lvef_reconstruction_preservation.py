from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import download_lvef_reconstruction_smoke as download
import capture_lvef_reconstruction_environment as environment_capture
import preserve_lvef_reconstruction_smoke as preserve


def _write_source_manifest(path: Path, *, include_checksum: bool = True) -> None:
    payload = b"payload"
    digest = hashlib.sha256(payload).hexdigest()
    columns = [
        "subject_id",
        "study_id",
        "split",
        "gcs_uri",
        "source_relative_path",
        "expected_size_bytes",
        "smoke_role",
    ]
    if include_checksum:
        columns.append("expected_sha256")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for index in range(4):
            subject_id = str(10000000 + index)
            study_id = str(20000000 + index)
            relative = (
                f"files/p{subject_id[:2]}/p{subject_id}/s{study_id}/clip{index}.dcm"
            )
            row = {
                "subject_id": subject_id,
                "study_id": study_id,
                "split": "train",
                "gcs_uri": f"gs://{download.MIMIC_ECHO_BUCKET}/{relative}",
                "source_relative_path": relative,
                "expected_size_bytes": len(payload),
                "smoke_role": download.EXPECTED_SMOKE_ROLES[index],
            }
            if include_checksum:
                row["expected_sha256"] = digest
            writer.writerow(row)


def _expect_download_error(code: str, function) -> None:
    try:
        function()
    except download.SmokeDownloadError as exc:
        assert exc.code == code
        return
    raise AssertionError(f"Expected SmokeDownloadError({code})")


def _expect_preservation_error(code: str, function) -> None:
    try:
        function()
    except preserve.PreservationError as exc:
        assert exc.code == code
        return
    raise AssertionError(f"Expected PreservationError({code})")


def test_source_manifest_requires_exact_four_train_studies_and_safe_paths() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest = root / "source.csv"
        _write_source_manifest(manifest)
        objects = download.load_source_manifest(manifest)
        assert len(objects) == 4
        assert len({item.study_id for item in objects}) == 4
        assert len({item.subject_id for item in objects}) == 4
        assert all(item.split == "train" for item in objects)
        assert all(item.relative_path.startswith("files/") for item in objects)

        rows = list(csv.DictReader(manifest.open(encoding="utf-8")))
        rows[0]["split"] = "test"
        bad_split = root / "bad_split.csv"
        with bad_split.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        _expect_download_error(
            "NONTRAIN_SMOKE_ROW", lambda: download.load_source_manifest(bad_split)
        )

        rows[0]["split"] = "train"
        rows[0]["gcs_uri"] = f"gs://{download.MIMIC_ECHO_BUCKET}/files/../escape.dcm"
        rows[0]["source_relative_path"] = "files/../escape.dcm"
        bad_path = root / "bad_path.csv"
        with bad_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        _expect_download_error(
            "UNSAFE_OBJECT_PATH", lambda: download.load_source_manifest(bad_path)
        )


def test_source_manifest_rejects_outcome_columns_and_partial_checksums() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest = root / "source.csv"
        _write_source_manifest(manifest)
        rows = list(csv.DictReader(manifest.open(encoding="utf-8")))

        for row in rows:
            row["lvef_label"] = "synthetic"
        forbidden = root / "forbidden.csv"
        with forbidden.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        _expect_download_error(
            "NONTECHNICAL_OR_UNKNOWN_MANIFEST_COLUMN",
            lambda: download.load_source_manifest(forbidden),
        )

        for row in rows:
            row.pop("lvef_label")
        rows[0]["expected_sha256"] = ""
        partial = root / "partial.csv"
        with partial.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        _expect_download_error(
            "PARTIAL_EXPECTED_CHECKSUM_COVERAGE",
            lambda: download.load_source_manifest(partial),
        )

        rows[0]["expected_sha256"] = rows[1]["expected_sha256"]
        rows[0]["smoke_role"] = rows[1]["smoke_role"]
        duplicate_role = root / "duplicate_role.csv"
        with duplicate_role.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        _expect_download_error(
            "NONBIJECTIVE_STUDY_SMOKE_ROLE",
            lambda: download.load_source_manifest(duplicate_role),
        )


def _synthetic_stat_block(
    item: download.SourceObject,
    *,
    payload: bytes = b"payload",
    include_md5: bool = True,
    include_crc32c: bool = True,
    include_generation: bool = True,
) -> str:
    md5 = base64.b64encode(
        hashlib.md5(payload, usedforsecurity=False).digest()
    ).decode("ascii")
    crc32c = base64.b64encode(b"\x01\x02\x03\x04").decode("ascii")
    lines = [f"{item.gcs_uri}:", f"    Content-Length: {len(payload)}"]
    if include_crc32c:
        lines.append(f"    Hash (crc32c): {crc32c}")
    if include_md5:
        lines.append(f"    Hash (md5): {md5}")
    if include_generation:
        lines.append("    Generation: 123456789")
    lines.append("    Content-Type: application/dicom")
    return "\n".join(lines) + "\n"


def test_remote_listing_and_exact_object_stat_reconciliation_are_exact() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest = root / "source.csv"
        _write_source_manifest(manifest, include_checksum=False)
        objects = download.load_source_manifest(manifest)
        lines = [
            f"7  2026-01-01T00:00:00Z  {item.gcs_uri}" for item in objects
        ]
        remote = download.parse_gsutil_ls_long("\n".join(lines) + "\nTOTAL: 28\n")
        assert set(remote) == {item.gcs_uri for item in objects}
        assert set(remote.values()) == {7}
        metadata = download.parse_gsutil_stat(
            "".join(_synthetic_stat_block(item) for item in objects)
        )
        assert set(metadata) == {item.gcs_uri for item in objects}
        assert all(item.size_bytes == 7 for item in metadata.values())
        assert all(item.md5_base64 for item in metadata.values())
        assert all(item.crc32c_base64 for item in metadata.values())
        assert all(item.generation == "123456789" for item in metadata.values())


def test_exact_object_stat_requires_md5_crc32c_and_generation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        manifest = Path(temporary) / "source.csv"
        _write_source_manifest(manifest, include_checksum=False)
        item = download.load_source_manifest(manifest)[0]
        cases = (
            ("REMOTE_STAT_MD5_MISSING", {"include_md5": False}),
            ("REMOTE_STAT_CRC32C_MISSING", {"include_crc32c": False}),
            ("REMOTE_STAT_GENERATION_MISSING", {"include_generation": False}),
        )
        for code, options in cases:
            _expect_download_error(
                code,
                lambda options=options: download.parse_gsutil_stat(
                    _synthetic_stat_block(item, **options)
                ),
            )


def test_listing_and_stat_size_disagreement_is_blocking() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        manifest = Path(temporary) / "source.csv"
        _write_source_manifest(manifest, include_checksum=False)
        objects = download.load_source_manifest(manifest)
        metadata = download.parse_gsutil_stat(
            "".join(_synthetic_stat_block(item) for item in objects)
        )
        listing_sizes = {item.gcs_uri: 7 for item in objects}
        listing_sizes[objects[0].gcs_uri] = 8
        _expect_download_error(
            "REMOTE_LISTING_STAT_SIZE_DISAGREEMENT",
            lambda: download.reconcile_remote_metadata(
                objects, listing_sizes, metadata
            ),
        )


def test_gpu_inventory_reconciles_exactly_one_scheduler_device() -> None:
    rows = environment_capture.parse_nvidia_smi_rows(
        "0, GPU-aaaa, NVIDIA A100, 550.54\n1, GPU-bbbb, NVIDIA A100, 550.54\n"
    )
    assert environment_capture.select_allocated_gpu(rows, "1")["uuid"] == "GPU-bbbb"
    assert environment_capture.select_allocated_gpu(rows, "GPU-aaaa")["index"] == "0"
    try:
        environment_capture.select_allocated_gpu(rows, "0,1")
    except environment_capture.EnvironmentCaptureError:
        pass
    else:
        raise AssertionError("Multiple visible accelerator tokens were accepted")


def test_complete_package_inventory_has_stable_nonempty_schema() -> None:
    inventory = environment_capture.installed_package_inventory()
    assert inventory
    assert inventory == sorted(
        inventory, key=lambda item: (item["name"].casefold(), item["version"])
    )
    assert all(set(item) == {"name", "version"} for item in inventory)
    assert all(item["name"] and item["version"] for item in inventory)


def test_exact_download_uses_bounded_manifest_and_emits_aggregate_only() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest = root / "source.csv"
        _write_source_manifest(manifest, include_checksum=False)
        fake_gsutil = root / "gsutil"
        objects = download.load_source_manifest(manifest)
        listing = "".join(
            f"7  2026-01-01T00:00:00Z  {item.gcs_uri}\n" for item in objects
        )
        stat_output = "".join(_synthetic_stat_block(item) for item in objects)
        fake_gsutil.write_text(
            """#!/bin/sh
set -eu
shift 2
command_name="$1"
shift
if [ "$command_name" = "ls" ]; then
  printf '%s' '"""
            + listing
            + """'
  exit 0
fi
if [ "$command_name" = "stat" ]; then
  printf '%s' '"""
            + stat_output
            + """'
  exit 0
fi
if [ "$command_name" = "cp" ]; then
  printf 'payload' > "$2"
  exit 0
fi
exit 9
""",
            encoding="utf-8",
        )
        fake_gsutil.chmod(fake_gsutil.stat().st_mode | stat.S_IXUSR)

        download_root = root / "download"
        restricted_report = root / "restricted" / "download_report.json"
        aggregate_output = root / "aggregate" / "download_summary.json"
        args = argparse.Namespace(
            source_manifest=manifest,
            expected_source_manifest_sha256=download.sha256_file(manifest),
            download_root=download_root,
            restricted_report=restricted_report,
            aggregate_output=aggregate_output,
            billing_project="synthetic-billing-project",
            preflight_only=False,
            gsutil_bin=str(fake_gsutil),
            bucket=download.MIMIC_ECHO_BUCKET,
        )
        original_minimum = download.MIN_FREE_BYTES
        download.MIN_FREE_BYTES = 0
        try:
            wrong_identity = argparse.Namespace(**vars(args))
            wrong_identity.expected_source_manifest_sha256 = "0" * 64
            _expect_download_error(
                "SOURCE_MANIFEST_SHA256_MISMATCH",
                lambda: download.execute(wrong_identity),
            )
            aggregate, restricted = download.execute(args)
        finally:
            download.MIN_FREE_BYTES = original_minimum
        assert aggregate["status"] == "PASS"
        assert aggregate["n_studies"] == 4
        assert aggregate["n_subjects"] == 4
        assert aggregate["n_expected_objects"] == 4
        assert aggregate["n_requested_objects"] == 4
        assert aggregate["n_downloaded_objects"] == 4
        assert aggregate["n_remote_metadata_complete"] == 4
        assert aggregate["n_remote_md5_verified_objects"] == 4
        assert aggregate["n_remote_metadata_mismatches"] == 0
        assert aggregate["n_local_sha256_computed"] == 4
        assert aggregate["total_downloaded_bytes"] == 28
        assert aggregate["remote_metadata_authority"] == "GCS_EXACT_OBJECT_STAT"
        assert aggregate["object_transport_integrity_status"] == "VERIFIED_ALL_OBJECTS"
        assert aggregate["exact_remote_set"] is True
        assert aggregate["exact_stat_set"] is True
        assert aggregate["listing_stat_sizes_match"] is True
        assert aggregate["all_remote_md5_present"] is True
        assert aggregate["all_local_md5_match"] is True
        assert aggregate["all_local_sha256_computed"] is True
        assert aggregate["no_extras"] is True
        assert aggregate["restricted_report_sha256"] == download.sha256_file(
            restricted_report
        )
        assert len(restricted["objects"]) == 4
        assert restricted["authority"] == "GCS_EXACT_OBJECT_STAT"
        assert all(row["remote_md5_verified"] for row in restricted["objects"])
        assert all(row["local_sha256"] for row in restricted["objects"])
        assert all(row["source_relative_path"] for row in restricted["objects"])
        assert len(list(download_root.rglob("*.dcm"))) == 4
        aggregate_text = json.dumps(aggregate, sort_keys=True)
        for forbidden in ("1000000", "2000000", "files/", "gs://", str(root)):
            assert forbidden not in aggregate_text


def test_download_rejects_local_md5_mismatch_against_gcs_stat() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest = root / "source.csv"
        _write_source_manifest(manifest, include_checksum=False)
        item = download.load_source_manifest(manifest)[0]
        local = root / "object.dcm"
        local.write_bytes(b"payload")
        wrong_md5 = base64.b64encode(
            hashlib.md5(b"wrong!!", usedforsecurity=False).digest()
        ).decode("ascii")
        metadata = download.GCSObjectMetadata(
            gcs_uri=item.gcs_uri,
            size_bytes=7,
            md5_base64=wrong_md5,
            crc32c_base64=base64.b64encode(b"\x01\x02\x03\x04").decode("ascii"),
            generation="123456789",
        )
        _expect_download_error(
            "DOWNLOADED_OBJECT_MD5_MISMATCH",
            lambda: download.verify_local_file(local, item, metadata),
        )


def test_exact_object_stat_rejects_missing_requested_uri() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest = root / "source.csv"
        _write_source_manifest(manifest, include_checksum=False)
        objects = download.load_source_manifest(manifest)
        fake_gsutil = root / "gsutil"
        incomplete = "".join(_synthetic_stat_block(item) for item in objects[:-1])
        fake_gsutil.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "printf '%s' '" + incomplete + "'\n",
            encoding="utf-8",
        )
        fake_gsutil.chmod(fake_gsutil.stat().st_mode | stat.S_IXUSR)
        _expect_download_error(
            "REMOTE_STAT_SET_MISMATCH",
            lambda: download.stat_remote_objects(
                objects,
                gsutil_bin=str(fake_gsutil),
                billing_project="synthetic-billing-project",
            ),
        )


def _preservation_args(root: Path, run_root: Path, checkpoint: Path) -> argparse.Namespace:
    config = root / "config.yaml"
    config.write_text("schema_version: 1\n", encoding="utf-8")
    provenance = run_root / "provenance"
    aggregate = run_root / "aggregate"
    provenance.mkdir(parents=True, exist_ok=True)
    aggregate.mkdir(parents=True, exist_ok=True)
    environment = provenance / "environment.json"
    python_executable = Path(sys.executable).resolve()
    script_sha256 = {
        name: preserve.sha256_file(ROOT / "scripts" / name)
        for name in preserve.REQUIRED_SCRIPT_IDENTITIES
    }
    environment.write_text(
        json.dumps(
            {
                "python_version": "3.10.12",
                "python_executable": str(python_executable),
                "python_executable_sha256": preserve.sha256_file(python_executable),
                "numpy_version": "synthetic",
                "pandas_version": "synthetic",
                "pydicom_version": "synthetic",
                "opencv_version": "synthetic",
                "torch_version": "synthetic",
                "torchvision_version": "synthetic",
                "scikit_learn_version": "synthetic",
                "cuda_version": "synthetic",
                "cudnn_version": "synthetic",
                "gpu_name": "synthetic",
                "gpu_uuid": "synthetic",
                "operating_system": "synthetic",
                "package_inventory": [
                    {"name": "synthetic-package", "version": "1.0"}
                ],
                "source_commit": "a" * 40,
                "repository_head": "a" * 40,
                "repository_branch": "codex/lvef-multitask-revalidation",
                "repository_clean": True,
                "config_sha256": preserve.sha256_file(config),
                "checkpoint_sha256": preserve.sha256_file(checkpoint),
                "technical_smoke_source_manifest_sha256": "b" * 64,
                "script_sha256": script_sha256,
                "scheduler": "SGE",
                "scheduler_job_id": "12345",
            }
        ),
        encoding="utf-8",
    )
    aggregate_hashes = {}
    for name, relative in preserve.AGGREGATE_ARTIFACT_RELATIVE_PATHS.items():
        path = run_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"artifact": name}) + "\n", encoding="utf-8")
        aggregate_hashes[name] = preserve.sha256_file(path)
    safety_gate = aggregate / "run_safety_gate.json"
    safety_gate.write_text(
        json.dumps(
            {
                "status": "PASS",
                "aggregate_safety_gate_passed": True,
                "technical_smoke_source_manifest_sha256": "b" * 64,
                "artifact_sha256": aggregate_hashes,
            }
        ),
        encoding="utf-8",
    )
    command_file = provenance / "job.sh"
    command_file.write_text("#!/bin/sh\nset -eu\n", encoding="utf-8")
    return argparse.Namespace(
        run_root=run_root,
        restricted_output_dir=root / "preservation",
        aggregate_output=root / "aggregate" / "preservation_safety.json",
        source_commit="a" * 40,
        config=config,
        checkpoint=checkpoint,
        expected_checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        environment_json=environment,
        aggregate_safety_gate_json=safety_gate,
        command_file=command_file,
        scheduler="SGE",
        job_id="12345",
        run_timestamp="2026-08-04T00:00:00Z",
    )


def _write_reproducibility_fixture(run_root: Path) -> None:
    reconstruction = preserve.reconstruction
    for run_name in ("run_a", "run_b"):
        run_dir = run_root / "restricted" / run_name
        extracted = run_dir / "extracted"
        extracted.mkdir(parents=True, exist_ok=True)
        extraction_rows = []
        for index in range(3):
            frames = np.full((2, 2, 2, 3), index + 1, dtype=np.uint8)
            sampled = np.asarray([0, 1], dtype=np.int64)
            source_frames = np.asarray([2], dtype=np.int32)
            relative = f"clip_{index}.npz"
            np.savez(
                extracted / relative,
                frames=frames,
                sampled_indices=sampled,
                source_num_frames=source_frames,
            )
            extraction_rows.append(
                {
                    "clip_key": f"clip_{index}",
                    "output_relative_path": relative,
                    "write_ok": True,
                    "frames_sha256": reconstruction.array_content_sha256(frames),
                    "sampled_indices_sha256": reconstruction.array_content_sha256(
                        sampled
                    ),
                    "source_num_frames_sha256": reconstruction.array_content_sha256(
                        source_frames
                    ),
                }
            )
        pd.DataFrame(extraction_rows).to_csv(
            run_dir / "extraction_manifest.csv", index=False
        )
        pd.DataFrame(
            {"clip_key": [f"clip_{index}" for index in range(3)], "embedding_idx": range(3)}
        ).to_csv(run_dir / "clip_embedding_manifest.csv", index=False)
        pd.DataFrame(
            {"study_key": [f"study_{index}" for index in range(3)], "embedding_idx": range(3)}
        ).to_csv(run_dir / "study_embedding_manifest.csv", index=False)
        clip_embeddings = np.arange(3 * 512, dtype=np.float32).reshape(3, 512)
        study_embeddings = (clip_embeddings + 0.5).astype(np.float32)
        np.savez(run_dir / "clip_embeddings_512.npz", embeddings=clip_embeddings)
        np.savez(run_dir / "study_embeddings_512.npz", embeddings=study_embeddings)

    restricted, aggregate = preserve._recompute_reproducibility(run_root)
    details_path = run_root / "restricted/reproducibility_details.json"
    aggregate_path = (
        run_root / preserve.AGGREGATE_ARTIFACT_RELATIVE_PATHS["reproducibility"]
    )
    aggregate_path.parent.mkdir(parents=True, exist_ok=True)
    reconstruction._write_outputs(
        details_path,
        aggregate_path,
        restricted,
        aggregate,
        bind_restricted_details=True,
    )


def _write_download_authority_fixture(
    root: Path,
) -> tuple[Path, Path, str, dict[str, object]]:
    reconstruction = preserve.reconstruction
    run_root = root / "run"
    source_dir = run_root / "restricted/source"
    source_dir.mkdir(parents=True)
    smoke_manifest = source_dir / "technical_smoke_source_manifest_restricted.csv"
    _write_source_manifest(smoke_manifest, include_checksum=False)
    smoke_sha256 = preserve.sha256_file(smoke_manifest)
    source = pd.read_csv(smoke_manifest, low_memory=False)
    download_root = run_root / "restricted/downloads"
    objects = []
    for record in source.to_dict(orient="records"):
        relative = str(record["source_relative_path"])
        path = download_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"payload")
        objects.append(
            {
                "subject_id": record["subject_id"],
                "study_id": record["study_id"],
                "split": record["split"],
                "smoke_role": record["smoke_role"],
                "source_relative_path": relative,
                "gcs_uri": f"gs://{reconstruction.MIMIC_ECHO_BUCKET}/{relative}",
                "remote_size_bytes": path.stat().st_size,
                "remote_stat_size_bytes": path.stat().st_size,
                "remote_md5_base64": reconstruction.md5_file_base64(path),
                "remote_crc32c_base64": "AAAAAA==",
                "remote_generation": "123456789",
                "local_size_bytes": path.stat().st_size,
                "local_status": "DOWNLOADED_VERIFIED",
                "local_md5_base64": reconstruction.md5_file_base64(path),
                "local_sha256": reconstruction.sha256_file(path),
                "remote_md5_verified": True,
            }
        )
    report = {
        "schema_version": 2,
        "status": "PASS",
        "authority": reconstruction.DOWNLOAD_REPORT_AUTHORITY,
        "object_transport_integrity_status": "VERIFIED_ALL_OBJECTS",
        "source_manifest_sha256": smoke_sha256,
        "objects": objects,
    }
    report.update(
        {
            "n_requested_objects": len(objects),
            "n_remote_metadata_complete": len(objects),
            "n_remote_md5_verified_objects": len(objects),
            "n_local_sha256_computed": len(objects),
        }
    )
    report_path = run_root / "restricted/download_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    frame, summary = reconstruction.audit_downloaded_objects(
        source,
        report,
        download_root,
        source_manifest_sha256=smoke_sha256,
    )
    frame.to_csv(run_root / "restricted/download_audit.csv", index=False)
    aggregate_dir = run_root / "aggregate"
    aggregate_dir.mkdir(parents=True)
    (aggregate_dir / "download_audit.json").write_text(
        json.dumps(summary, sort_keys=True), encoding="utf-8"
    )
    download_summary: dict[str, object] = {
        "status": "PASS",
        "source_manifest_sha256": smoke_sha256,
        "source_manifest_sha256_verified": True,
        "remote_metadata_authority": reconstruction.DOWNLOAD_REPORT_AUTHORITY,
        "object_transport_integrity_status": "VERIFIED_ALL_OBJECTS",
        "restricted_report_sha256": preserve.sha256_file(report_path),
    }
    return run_root, smoke_manifest, smoke_sha256, download_summary


def test_preservation_revalidates_gcs_md5_and_local_sha256_authority() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root, manifest, manifest_sha, download_summary = (
            _write_download_authority_fixture(root)
        )
        frame, summary = preserve._revalidate_download_authority(
            run_root, manifest, manifest_sha, download_summary
        )
        assert frame["download_ok"].all()
        assert summary["download_integrity_status"] == (
            "PASS_GCS_METADATA_AND_LOCAL_HASH"
        )

        first = next((run_root / "restricted/downloads").rglob("*.dcm"))
        first.write_bytes(b"mutated-after-audit")
        _expect_preservation_error(
            "DOWNLOAD_AUTHORITY_REVALIDATION_FAILED",
            lambda: preserve._revalidate_download_authority(
                run_root, manifest, manifest_sha, download_summary
            ),
        )


def test_preservation_rejects_mutated_downloader_authority_report() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root, manifest, manifest_sha, download_summary = (
            _write_download_authority_fixture(root)
        )
        report_path = run_root / "restricted/download_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["objects"][0]["remote_generation"] = "not-a-generation"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        _expect_preservation_error(
            "DOWNLOAD_REPORT_IDENTITY_MISMATCH",
            lambda: preserve._revalidate_download_authority(
                run_root, manifest, manifest_sha, download_summary
            ),
        )


def test_preservation_rejects_coordinated_download_report_and_audit_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root, manifest, manifest_sha, download_summary = (
            _write_download_authority_fixture(root)
        )
        report_path = run_root / "restricted/download_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        first_record = report["objects"][0]
        first = (
            run_root
            / "restricted/downloads"
            / first_record["source_relative_path"]
        )
        assert len(first.read_bytes()) == len(b"changed")
        first.write_bytes(b"changed")
        first_record["remote_md5_base64"] = preserve.reconstruction.md5_file_base64(
            first
        )
        first_record["local_md5_base64"] = first_record["remote_md5_base64"]
        first_record["local_sha256"] = preserve.reconstruction.sha256_file(first)
        report_path.write_text(json.dumps(report), encoding="utf-8")

        source = pd.read_csv(manifest, low_memory=False)
        mutated_frame, mutated_summary = (
            preserve.reconstruction.audit_downloaded_objects(
                source,
                report,
                run_root / "restricted/downloads",
                source_manifest_sha256=manifest_sha,
            )
        )
        mutated_frame.to_csv(run_root / "restricted/download_audit.csv", index=False)
        saved_summary = json.loads(
            (run_root / "aggregate/download_audit.json").read_text(encoding="utf-8")
        )
        assert mutated_summary == saved_summary

        _expect_preservation_error(
            "DOWNLOAD_REPORT_IDENTITY_MISMATCH",
            lambda: preserve._revalidate_download_authority(
                run_root, manifest, manifest_sha, download_summary
            ),
        )


def test_aggregate_artifact_hash_reconciliation_detects_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        run_root = Path(temporary)
        declared = {}
        for name, relative in preserve.AGGREGATE_ARTIFACT_RELATIVE_PATHS.items():
            path = run_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"name": name}), encoding="utf-8")
            declared[name] = preserve.sha256_file(path)
        safety = {"artifact_sha256": declared}
        assert preserve._verify_aggregate_artifact_hashes(run_root, safety) == declared
        changed = run_root / preserve.AGGREGATE_ARTIFACT_RELATIVE_PATHS["download"]
        changed.write_text('{"changed":true}', encoding="utf-8")
        _expect_preservation_error(
            "AGGREGATE_ARTIFACT_CHANGED_AFTER_SAFETY_GATE",
            lambda: preserve._verify_aggregate_artifact_hashes(run_root, safety),
        )


def test_reproducibility_revalidation_detects_post_comparison_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        run_root = Path(temporary)
        _write_reproducibility_fixture(run_root)
        preserve._verify_reproducibility_matches_saved(run_root)
        changed = run_root / "restricted/run_b/clip_embeddings_512.npz"
        with np.load(changed, allow_pickle=False) as archive:
            embeddings = archive["embeddings"].copy()
        embeddings[0, 0] += np.float32(1.0)
        np.savez(changed, embeddings=embeddings)
        _expect_preservation_error(
            "REPRODUCIBILITY_DETAILS_REVALIDATION_MISMATCH",
            lambda: preserve._verify_reproducibility_matches_saved(run_root),
        )


def test_reproducibility_rejects_coordinated_extraction_and_details_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        run_root = Path(temporary)
        _write_reproducibility_fixture(run_root)
        aggregate_path = (
            run_root / preserve.AGGREGATE_ARTIFACT_RELATIVE_PATHS["reproducibility"]
        )
        saved_aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))

        for run_name in ("run_a", "run_b"):
            run_dir = run_root / "restricted" / run_name
            manifest_path = run_dir / "extraction_manifest.csv"
            manifest = pd.read_csv(manifest_path, low_memory=False)
            first = manifest.iloc[0]
            npz_path = run_dir / "extracted" / first["output_relative_path"]
            with np.load(npz_path, allow_pickle=False) as archive:
                frames = archive["frames"].copy()
                sampled_indices = archive["sampled_indices"].copy()
                source_num_frames = archive["source_num_frames"].copy()
            frames[0, 0, 0, 0] ^= np.uint8(1)
            np.savez(
                npz_path,
                frames=frames,
                sampled_indices=sampled_indices,
                source_num_frames=source_num_frames,
            )
            manifest.loc[0, "frames_sha256"] = (
                preserve.reconstruction.array_content_sha256(frames)
            )
            manifest.loc[0, "npz_sha256"] = preserve.reconstruction.sha256_file(
                npz_path
            )
            manifest.to_csv(manifest_path, index=False)

        mutated_details, mutated_aggregate = preserve._recompute_reproducibility(
            run_root
        )
        comparable_saved = dict(saved_aggregate)
        comparable_saved.pop("restricted_details_sha256")
        assert mutated_aggregate == comparable_saved
        details_path = run_root / "restricted/reproducibility_details.json"
        details_path.write_text(
            json.dumps(mutated_details, sort_keys=True), encoding="utf-8"
        )

        _expect_preservation_error(
            "REPRODUCIBILITY_DETAILS_IDENTITY_MISMATCH",
            lambda: preserve._verify_reproducibility_matches_saved(run_root),
        )


def test_preservation_pack_uses_relative_paths_and_second_pass_hashes() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root = root / "run"
        (run_root / "nested").mkdir(parents=True)
        (run_root / "first.json").write_text('{"status":"PASS"}\n', encoding="utf-8")
        (run_root / "nested" / "array.npz").write_bytes(b"synthetic-array")
        checkpoint = root / "echo_prime_encoder.pt"
        checkpoint.write_bytes(b"synthetic-checkpoint")
        args = _preservation_args(root, run_root, checkpoint)
        original_bytes = preserve.CHECKPOINT_BYTES
        original_sha256 = preserve.CHECKPOINT_SHA256
        preserve.CHECKPOINT_BYTES = checkpoint.stat().st_size
        preserve.CHECKPOINT_SHA256 = preserve.sha256_file(checkpoint)
        original_revalidation = preserve.revalidate_authoritative_run_artifacts
        preserve.revalidate_authoritative_run_artifacts = (
            lambda run_root, safety_gate: (
                {
                    "aggregate_artifact_hashes_reconciled": 12,
                    "download_audit_recomputed": True,
                    "dicom_audit_recomputed": True,
                    "reproducibility_recomputed": True,
                    "restricted_files_snapshotted": 0,
                },
                {},
            )
        )
        try:
            aggregate = preserve.create_preservation_pack(args)
        finally:
            preserve.revalidate_authoritative_run_artifacts = original_revalidation
            preserve.CHECKPOINT_BYTES = original_bytes
            preserve.CHECKPOINT_SHA256 = original_sha256
        assert aggregate["status"] == "PASS"
        assert aggregate["n_files"] == 17
        assert aggregate["exact_file_set"] is True
        assert aggregate["all_sizes_match"] is True
        assert aggregate["all_sha256_match"] is True
        assert aggregate["no_symlinks"] is True
        assert aggregate["aggregate_artifact_hashes_reconciled"] is True
        assert aggregate["restricted_snapshot_reconciled"] is True

        manifest_path = args.restricted_output_dir / preserve.MANIFEST_NAME
        records = preserve.read_manifest(manifest_path)
        expected_paths = [
            *preserve.AGGREGATE_ARTIFACT_RELATIVE_PATHS.values(),
            "aggregate/run_safety_gate.json",
            "first.json",
            "nested/array.npz",
            "provenance/environment.json",
            "provenance/job.sh",
        ]
        assert [item.relative_path for item in records] == sorted(expected_paths)
        verified = preserve.verify_manifest_independently(run_root, manifest_path)
        assert verified["status"] == "PASS"

        metadata = json.loads(
            (args.restricted_output_dir / preserve.METADATA_NAME).read_text(encoding="utf-8")
        )
        assert metadata["source_commit"] == "a" * 40
        assert metadata["checkpoint"]["sha256"] == hashlib.sha256(
            checkpoint.read_bytes()
        ).hexdigest()
        assert metadata["environment"]["python_version"] == "3.10.12"

        aggregate_text = json.dumps(aggregate, sort_keys=True)
        for forbidden in (str(root), "first.json", "array.npz", "12345"):
            assert forbidden not in aggregate_text


def test_preservation_reconciles_scheduler_identity_with_environment() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root = root / "run"
        run_root.mkdir()
        (run_root / "artifact.bin").write_bytes(b"synthetic")
        checkpoint = root / "echo_prime_encoder.pt"
        checkpoint.write_bytes(b"synthetic-checkpoint")
        args = _preservation_args(root, run_root, checkpoint)
        environment = json.loads(args.environment_json.read_text(encoding="utf-8"))
        environment["scheduler_job_id"] = "99999"
        args.environment_json.write_text(json.dumps(environment), encoding="utf-8")
        original_bytes = preserve.CHECKPOINT_BYTES
        original_sha256 = preserve.CHECKPOINT_SHA256
        preserve.CHECKPOINT_BYTES = checkpoint.stat().st_size
        preserve.CHECKPOINT_SHA256 = preserve.sha256_file(checkpoint)
        try:
            _expect_preservation_error(
                "ENVIRONMENT_JOB_ID_MISMATCH",
                lambda: preserve.create_preservation_pack(args),
            )
        finally:
            preserve.CHECKPOINT_BYTES = original_bytes
            preserve.CHECKPOINT_SHA256 = original_sha256


def test_independent_verification_detects_post_manifest_mutation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run_root = root / "run"
        run_root.mkdir()
        artifact = run_root / "artifact.bin"
        artifact.write_bytes(b"before")
        records = preserve.build_file_records(run_root)
        manifest = root / "manifest.tsv"
        preserve.write_manifest_exclusive(manifest, records)
        artifact.write_bytes(b"after")
        _expect_preservation_error(
            "SIZE_MISMATCH_DURING_VERIFICATION",
            lambda: preserve.verify_manifest_independently(run_root, manifest),
        )


def test_preservation_rejects_symlinks_and_unsafe_names() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        outside = root / "outside.bin"
        outside.write_bytes(b"outside")
        run_root = root / "run"
        run_root.mkdir()
        symlink = run_root / "linked.bin"
        try:
            symlink.symlink_to(outside)
        except (OSError, NotImplementedError):
            return
        _expect_preservation_error(
            "SYMLINK_IN_RUN_SCOPE", lambda: preserve.build_file_records(run_root)
        )
