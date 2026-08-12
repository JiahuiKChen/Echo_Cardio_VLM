from __future__ import annotations

# SYNTHETIC_CONTROL_PLANE_ONLY: no SCC, cloud, scheduler, DICOM, or GPU.

from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lvef_c3_canary as canary


def _load_d3_fixture_module():
    path = ROOT / "tests" / "test_lvef_c3_phase1ef_d3_recovery.py"
    spec = importlib.util.spec_from_file_location("canary_d3_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


D3_FIXTURE = _load_d3_fixture_module()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _expect_code(code: str, operation) -> None:
    try:
        operation()
    except canary.CanaryControlError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected CanaryControlError {code}")


def _write(path: Path, payload: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)
    return path


def _gcloud_receipt(executable: Path) -> dict[str, object]:
    packet = canary.production_authority_packet
    return {
        "audit": "lvef_scc_gcloud_resolution",
        "credential_material_accessed": False,
        "expected_version": packet.PINNED_GCLOUD_VERSION,
        "executable_sha256": _sha(executable.read_bytes()),
        "module_name": None,
        "resolution_source": "COMMON_SELF_CONTAINED_INSTALL",
        "retained_archive_sha256": packet.PINNED_GCLOUD_ARCHIVE_SHA256,
        "retained_tar_payload_sha256": packet.PINNED_GCLOUD_TAR_PAYLOAD_SHA256,
        "selected_executable": str(executable.resolve()),
        "status": "PASS",
        "version": packet.PINNED_GCLOUD_VERSION,
    }


def _packet(
    fixture,
    *, checkpoint: Path,
    selected: Path,
    source: Path,
    metadata: Path,
    split: Path,
    gcloud: Path,
    gcloud_receipt: Path,
) -> dict[str, object]:
    packet = canary.production_authority_packet
    authority = {
        role: {"size_bytes": 1, "sha256": "f" * 64}
        for role in packet.REQUIRED_ROLES
    }
    for role, path in (
        ("checkpoint", checkpoint),
        ("selected_study_manifest", selected),
        ("selected_source_manifest", source),
        ("selected_source_metadata_receipt", metadata),
        ("split_map", split),
        ("gcloud_executable", gcloud),
        ("gcloud_resolution_receipt", gcloud_receipt),
        ("cloudsdk_config_receipt", gcloud_receipt),
    ):
        authority[role] = {
            "size_bytes": path.stat().st_size,
            "sha256": _sha(path.read_bytes()),
        }
    return {
        "schema_version": packet.SCHEMA_VERSION,
        "artifact_type": packet.ARTIFACT_TYPE,
        "status": packet.STATUS,
        "attempt_id": fixture.config_state.prior_production_attempt_id,
        "created_at_utc": "2026-08-12T12:00:00+00:00",
        "governing_commit": "b" * 40,
        "authority": authority,
        "semantic_validation": {
            key: True for key in packet.SEMANTIC_VALIDATION_KEYS
        },
        "authorization_scopes": {
            key: False for key in packet.AUTHORIZATION_SCOPES
        },
        "execution_attestations": dict(packet.EXECUTION_ATTESTATIONS),
        "full_c3_status": "NO_GO_PENDING_OWNER_REVIEW",
    }


def _prepare_private_fixture(fixture) -> tuple[canary.CanaryPrivateAuthorityConfig, set[Path], ExitStack]:
    selected = _write(fixture.root / "authority" / "selected.csv", b"selected rows\n")
    source = _write(fixture.root / "authority" / "source.csv", b"source rows\n")
    metadata = _write(
        fixture.root / "authority" / "metadata.jsonl", b"metadata rows\n"
    )
    split = _write(fixture.root / "authority" / "split.csv", b"split rows\n")
    checkpoint = _write(
        fixture.root / "authority" / "echo_prime_encoder.pt", b"checkpoint bytes\n"
    )

    gcloud_root = fixture.root / "google-cloud-cli-579.0.0"
    gcloud = _write(gcloud_root / "bin" / "gcloud", b"#!/bin/sh\nexit 0\n", 0o700)
    receipt_value = _gcloud_receipt(gcloud)
    gcloud_receipt = _write(
        fixture.root / "private" / "gcloud-resolution.json",
        (json.dumps(receipt_value, sort_keys=True) + "\n").encode(),
    )
    cloudsdk = fixture.root / "private" / "cloudsdk"
    cloudsdk.mkdir(mode=0o700)
    _write(
        cloudsdk / "application_default_credentials.json",
        b"synthetic credential placeholder\n",
    )

    state = canary.phase1eg_authority.load_canonical_state(fixture.config)
    fixture.config_state = state
    packet_value = _packet(
        fixture,
        checkpoint=checkpoint,
        selected=selected,
        source=source,
        metadata=metadata,
        split=split,
        gcloud=gcloud,
        gcloud_receipt=gcloud_receipt,
    )
    packet_payload = (json.dumps(packet_value, sort_keys=True) + "\n").encode()
    packet_path = _write(
        fixture.root / "private" / "production-packet.json", packet_payload
    )

    additions = {
        "PRIOR_PRODUCTION_PACKET": str(packet_path),
        "PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE": str(len(packet_payload)),
        "PRIOR_PRODUCTION_PACKET_EXPECTED_SHA": _sha(packet_payload),
        "CHECKPOINT": str(checkpoint),
        "CHECKPOINT_EXPECTED_SIZE": str(checkpoint.stat().st_size),
        "CHECKPOINT_EXPECTED_SHA": _sha(checkpoint.read_bytes()),
        "SELECTED_STUDIES": str(selected),
        "SELECTED_STUDIES_EXPECTED_SIZE": str(selected.stat().st_size),
        "SELECTED_STUDIES_EXPECTED_SHA": _sha(selected.read_bytes()),
        "SELECTED_SOURCE": str(source),
        "SELECTED_SOURCE_EXPECTED_SIZE": str(source.stat().st_size),
        "SELECTED_SOURCE_EXPECTED_SHA": _sha(source.read_bytes()),
        "SOURCE_METADATA": str(metadata),
        "SOURCE_METADATA_EXPECTED_SIZE": str(metadata.stat().st_size),
        "SOURCE_METADATA_EXPECTED_SHA": _sha(metadata.read_bytes()),
        "SPLIT_MAP": str(split),
        "SPLIT_MAP_EXPECTED_SIZE": str(split.stat().st_size),
        "SPLIT_MAP_EXPECTED_SHA": _sha(split.read_bytes()),
        "GCLOUD": str(gcloud),
        "GCLOUD_RECEIPT": str(gcloud_receipt),
        "CLOUDSDK_CONFIG": str(cloudsdk),
        "LVEF_C3_GCP_BILLING_PROJECT": "synthetic-private-project",
    }
    existing = fixture.preparation_environment.read_text(encoding="utf-8")
    fixture.preparation_environment.write_text(
        existing + "".join(f"{key}={value}\n" for key, value in additions.items()),
        encoding="utf-8",
    )
    fixture.preparation_environment.chmod(0o600)
    state_value = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state_value["preparation_environment_bytes"] = (
        fixture.preparation_environment.stat().st_size
    )
    fixture.state_path.write_text(
        json.dumps(state_value, indent=2) + "\n", encoding="utf-8"
    )
    fixture.state_path.chmod(0o644)

    patches = ExitStack()
    patches.enter_context(fixture.patches())
    patches.enter_context(
        mock.patch.object(
            canary, "PHASE1EG_PRODUCTION_PACKET_BYTES", len(packet_payload)
        )
    )
    patches.enter_context(
        mock.patch.object(
            canary, "PHASE1EG_PRODUCTION_PACKET_SHA256", _sha(packet_payload)
        )
    )
    patches.enter_context(
        mock.patch.object(
            canary.production_authority_packet, "PINNED_GCLOUD_ROOT", str(gcloud_root)
        )
    )
    patches.enter_context(
        mock.patch.object(
            canary.orchestration_core,
            "EXPECTED_CHECKPOINT_SHA256",
            additions["CHECKPOINT_EXPECTED_SHA"],
        )
    )
    patches.enter_context(
        mock.patch.object(
            canary.orchestration_core,
            "EXPECTED_SELECTED_MANIFEST_SHA256",
            additions["SELECTED_STUDIES_EXPECTED_SHA"],
        )
    )
    patches.enter_context(
        mock.patch.object(
            canary.orchestration_core,
            "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
            additions["SELECTED_SOURCE_EXPECTED_SHA"],
        )
    )
    patches.enter_context(
        mock.patch.object(
            canary.orchestration_core,
            "EXPECTED_SPLIT_MAP_SHA256",
            additions["SPLIT_MAP_EXPECTED_SHA"],
        )
    )
    current_receipt = (
        fixture.diagnostic_root
        / f"current_environment_{canary.PHASE1HR1_STARTING_AUTHORITY_COMMIT}.restricted.json"
    )
    with fixture.patches():
        receipt_payload = fixture.write_receipt(
            current_receipt, canary.PHASE1HR1_STARTING_AUTHORITY_COMMIT
        )
    config = canary.CanaryPrivateAuthorityConfig(
        recovery=fixture.config,
        current_environment_commit=canary.PHASE1HR1_STARTING_AUTHORITY_COMMIT,
        current_environment_bytes=len(receipt_payload),
        current_environment_sha256=_sha(receipt_payload),
    )
    return config, {selected, source, metadata, split}, patches


def test_local_non_scc_private_authority_is_safely_unavailable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        recovery = canary.phase1eg_authority.RecoveryConfig(
            worktree=root / "missing-worktree",
            audit_root=root / "missing-audits",
            production_root=root / "missing-production",
            lexical_python=root / "missing-python",
            state_path=root / "missing-state.json",
        )
        with mock.patch.object(
            canary.phase1eg_authority,
            "load_canonical_state",
            side_effect=AssertionError("local unavailable must not inspect private state"),
        ):
            result = canary.validate_scc_private_authority(
                canary.CanaryPrivateAuthorityConfig(recovery=recovery)
            )
    assert result["status"] == "UNAVAILABLE_LOCAL_NON_SCC"
    assert result["scc_private_authority_required"] is False
    assert result["restricted_row_bodies_read"] == 0


def test_scc_private_authority_passes_without_hashing_restricted_row_bodies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = D3_FIXTURE.D3Fixture(Path(directory))
        config, row_paths, patches = _prepare_private_fixture(fixture)
        original_sha256 = canary.phase1eg_authority.sha256_file

        def guarded_sha256(path: Path) -> str:
            if path in row_paths:
                raise AssertionError("restricted row body was hashed")
            return original_sha256(path)

        with patches, mock.patch.object(
            canary.phase1eg_authority, "sha256_file", side_effect=guarded_sha256
        ):
            result = canary.validate_scc_private_authority(config)
    assert result == canary._pass_private_authority_summary()
    assert result["restricted_row_bodies_read"] == 0
    assert result["cloud_requests"] == result["qsub_submissions"] == 0


def test_scc_private_authority_fails_on_attempt_or_environment_seal_drift() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = D3_FIXTURE.D3Fixture(Path(directory))
        config, _, patches = _prepare_private_fixture(fixture)
        fixture.attempt_roots[4].mkdir(mode=0o700)
        with patches:
            _expect_code(
                "CANARY_SCC_PRIVATE_AUTHORITY_INVALID",
                lambda: canary.validate_scc_private_authority(config),
            )

    with tempfile.TemporaryDirectory() as directory:
        fixture = D3_FIXTURE.D3Fixture(Path(directory))
        config, _, patches = _prepare_private_fixture(fixture)
        changed = canary.CanaryPrivateAuthorityConfig(
            recovery=config.recovery,
            current_environment_commit=config.current_environment_commit,
            current_environment_bytes=config.current_environment_bytes,
            current_environment_sha256="0" * 64,
        )
        with patches:
            _expect_code(
                "CANARY_CURRENT_ENVIRONMENT_BINDING_MISMATCH",
                lambda: canary.validate_scc_private_authority(changed),
            )


def test_preflight_scope_blocks_before_private_or_synthetic_validation() -> None:
    state = SimpleNamespace(permits=lambda scope: False)
    with mock.patch.object(
        canary.execution_state, "load_execution_state", return_value=state
    ), mock.patch.object(
        canary, "validate_installation", side_effect=AssertionError("too late")
    ), mock.patch.object(
        canary, "validate_scc_private_authority", side_effect=AssertionError("too late")
    ):
        _expect_code("CANARY_PREFLIGHT_SCOPE_NOT_PERMITTED", canary.preflight_only)


def test_installation_requires_phase1hr1_starting_commit_ancestry() -> None:
    head = "c" * 40
    state = SimpleNamespace(
        branch=canary.REQUIRED_BRANCH,
        starting_authority_commit="a" * 40,
    )
    calls: list[tuple[str, ...]] = []

    def run_git(arguments):
        arguments = tuple(arguments)
        calls.append(arguments)
        if arguments == ("branch", "--show-current"):
            return canary.REQUIRED_BRANCH
        if arguments == ("rev-parse", "HEAD"):
            return head
        if arguments == (
            "rev-parse", f"refs/remotes/origin/{canary.REQUIRED_BRANCH}"
        ):
            return head
        return ""

    with mock.patch.object(
        canary.execution_state, "load_execution_state", return_value=state
    ), mock.patch.object(canary, "_run_git", side_effect=run_git), mock.patch.object(
        canary.orchestration_core,
        "load_orchestration_contract",
        return_value={"cohort": {"release": canary.manifest_contract.SOURCE_RELEASE}},
    ), mock.patch.object(
        canary.scheduler, "load_scheduler_plan", return_value={}
    ), mock.patch.object(canary.scheduler, "validate_scheduler_plan"):
        result = canary.validate_installation()
    assert result["status"] == "PASS_INSTALLATION_VALIDATION_NO_LIVE_OPERATIONS"
    assert (
        "merge-base",
        "--is-ancestor",
        canary.PHASE1HR1_STARTING_AUTHORITY_COMMIT,
        head,
    ) in calls
