from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile


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


def test_remote_listing_and_release_checksum_reconciliation_are_exact() -> None:
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

        checksum_file = root / "SHA256SUMS.txt"
        digest = hashlib.sha256(b"payload").hexdigest()
        checksum_file.write_text(
            "".join(f"{digest}  ./{item.relative_path}\n" for item in objects),
            encoding="utf-8",
        )
        checksums = download.parse_release_checksums(checksum_file)
        updated = download.apply_release_checksums(objects, checksums)
        assert all(item.expected_sha256 == digest for item in updated)


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
        _write_source_manifest(manifest)
        fake_gsutil = root / "gsutil"
        objects = download.load_source_manifest(manifest)
        listing = "".join(
            f"7  2026-01-01T00:00:00Z  {item.gcs_uri}\n" for item in objects
        )
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
        release_checksums = root / "SHA256SUMS.txt"
        digest = hashlib.sha256(b"payload").hexdigest()
        release_checksums.write_text(
            "".join(f"{digest}  ./{item.relative_path}\n" for item in objects),
            encoding="utf-8",
        )
        args = argparse.Namespace(
            source_manifest=manifest,
            expected_source_manifest_sha256=download.sha256_file(manifest),
            download_root=download_root,
            restricted_report=restricted_report,
            aggregate_output=aggregate_output,
            billing_project="synthetic-billing-project",
            release_checksums=release_checksums,
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
            without_release = argparse.Namespace(**vars(args))
            without_release.release_checksums = None
            _expect_download_error(
                "RELEASE_CHECKSUMS_REQUIRED_FOR_DOWNLOAD",
                lambda: download.execute(without_release),
            )
            aggregate, restricted = download.execute(args)
        finally:
            download.MIN_FREE_BYTES = original_minimum
        assert aggregate["status"] == "PASS"
        assert aggregate["n_studies"] == 4
        assert aggregate["n_subjects"] == 4
        assert aggregate["n_expected_objects"] == 4
        assert aggregate["n_downloaded_objects"] == 4
        assert aggregate["n_checksum_verified_objects"] == 4
        assert aggregate["checksum_authority_status"] == "VERIFIED_ALL_OBJECTS"
        assert aggregate["exact_remote_set"] is True
        assert aggregate["no_extras"] is True
        assert len(restricted["objects"]) == 4
        assert len(list(download_root.rglob("*.dcm"))) == 4
        aggregate_text = json.dumps(aggregate, sort_keys=True)
        for forbidden in ("1000000", "2000000", "files/", "gs://", str(root)):
            assert forbidden not in aggregate_text


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
    safety_gate = aggregate / "run_safety_gate.json"
    safety_gate.write_text(
        json.dumps(
            {
                "status": "PASS",
                "aggregate_safety_gate_passed": True,
                "technical_smoke_source_manifest_sha256": "b" * 64,
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
        try:
            aggregate = preserve.create_preservation_pack(args)
        finally:
            preserve.CHECKPOINT_BYTES = original_bytes
            preserve.CHECKPOINT_SHA256 = original_sha256
        assert aggregate["status"] == "PASS"
        assert aggregate["n_files"] == 5
        assert aggregate["exact_file_set"] is True
        assert aggregate["all_sizes_match"] is True
        assert aggregate["all_sha256_match"] is True
        assert aggregate["no_symlinks"] is True

        manifest_path = args.restricted_output_dir / preserve.MANIFEST_NAME
        records = preserve.read_manifest(manifest_path)
        assert [item.relative_path for item in records] == [
            "aggregate/run_safety_gate.json",
            "first.json",
            "nested/array.npz",
            "provenance/environment.json",
            "provenance/job.sh",
        ]
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
