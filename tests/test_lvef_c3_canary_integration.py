from __future__ import annotations

"""One dependency-light, synthetic, production-faithful canary integration.

The fixture contains synthetic identifiers and pixels only.  Every operational
edge is an in-process hook: it has no cloud client, scheduler command, SCC
access, credential lookup, restricted DICOM body read, GPU execution, model
fitting, prediction, or confirmatory-performance path.  A standards-shaped
minimal Part-10 byte fixture is read only inside a private temporary directory.
"""

import base64
from collections import deque
import copy
from contextlib import contextmanager
import csv
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Iterator, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_canary as integration
import lvef_c3_canary_manifest as manifest_contract
import lvef_c3_canary_scheduler_plan as scheduler
import lvef_c3_orchestration_core as orchestration_core
import lvef_c3_production_stages as production_stages
import lvef_reconstruction_smoke as reconstruction
import preserve_lvef_c3_production_batch as preservation


COMMIT = "a" * 40
SOURCE_MANIFEST_SHA256 = "b" * 64
CONFIGURATION_HASHES = {
    "execution_state": "c" * 64,
    "production_contract": "d" * 64,
}
FORBIDDEN_ROLES = frozenset(
    {
        "MODEL_FITTING",
        "ENDPOINT_PREDICTION",
        "CONFIRMATORY_PERFORMANCE",
        "PRODUCTION_CONTINUATION",
    }
)


_LONG_VALUE_REPRESENTATIONS = frozenset(
    {"OB", "OD", "OF", "OL", "OW", "SQ", "UC", "UR", "UT", "UN"}
)


def _dicom_text(value: str, *, null_pad: bool = False) -> bytes:
    encoded = value.encode("ascii")
    if len(encoded) % 2:
        encoded += b"\0" if null_pad else b" "
    return encoded


def _dicom_element(group: int, element: int, vr: str, value: bytes) -> bytes:
    if len(value) % 2:
        raise AssertionError("synthetic DICOM values must already be even length")
    tag_and_vr = struct.pack("<HH", group, element) + vr.encode("ascii")
    if vr in _LONG_VALUE_REPRESENTATIONS:
        return tag_and_vr + b"\0\0" + struct.pack("<I", len(value)) + value
    if len(value) > 0xFFFF:
        raise AssertionError("short-VR synthetic DICOM value is too large")
    return tag_and_vr + struct.pack("<H", len(value)) + value


def _minimal_part10_dicom_bytes(pixel_offset: int) -> bytes:
    """Build one explicit-VR little-endian multiframe RGB Part-10 object."""

    sop_class = "1.2.840.10008.5.1.4.1.1.3.1"
    sop_instance = "1.2.826.0.1.3680043.10.543.1"
    transfer_syntax = "1.2.840.10008.1.2.1"
    implementation = "1.2.826.0.1.3680043.10.543.2"
    meta_tail = b"".join(
        (
            _dicom_element(0x0002, 0x0001, "OB", b"\0\1"),
            _dicom_element(
                0x0002, 0x0002, "UI", _dicom_text(sop_class, null_pad=True)
            ),
            _dicom_element(
                0x0002, 0x0003, "UI", _dicom_text(sop_instance, null_pad=True)
            ),
            _dicom_element(
                0x0002,
                0x0010,
                "UI",
                _dicom_text(transfer_syntax, null_pad=True),
            ),
            _dicom_element(
                0x0002,
                0x0012,
                "UI",
                _dicom_text(implementation, null_pad=True),
            ),
        )
    )
    meta = _dicom_element(
        0x0002, 0x0000, "UL", struct.pack("<I", len(meta_tail))
    ) + meta_tail

    frames = np.zeros((4, 224, 224, 3), dtype=np.uint8)
    for frame_index in range(4):
        value = np.uint8(40 + pixel_offset * 3 + frame_index * 20)
        frames[frame_index, 30:194, 30:194, :] = value
    dataset = b"".join(
        (
            _dicom_element(
                0x0008, 0x0016, "UI", _dicom_text(sop_class, null_pad=True)
            ),
            _dicom_element(
                0x0008, 0x0018, "UI", _dicom_text(sop_instance, null_pad=True)
            ),
            _dicom_element(0x0028, 0x0002, "US", struct.pack("<H", 3)),
            _dicom_element(0x0028, 0x0004, "CS", _dicom_text("RGB")),
            _dicom_element(0x0028, 0x0006, "US", struct.pack("<H", 0)),
            _dicom_element(0x0028, 0x0008, "IS", _dicom_text("4")),
            _dicom_element(0x0028, 0x0010, "US", struct.pack("<H", 224)),
            _dicom_element(0x0028, 0x0011, "US", struct.pack("<H", 224)),
            _dicom_element(0x0028, 0x0100, "US", struct.pack("<H", 8)),
            _dicom_element(0x0028, 0x0101, "US", struct.pack("<H", 8)),
            _dicom_element(0x0028, 0x0102, "US", struct.pack("<H", 7)),
            _dicom_element(0x0028, 0x0103, "US", struct.pack("<H", 0)),
            _dicom_element(0x7FE0, 0x0010, "OB", frames.tobytes(order="C")),
        )
    )
    return b"\0" * 128 + b"DICM" + meta + dataset


SYNTHETIC_DICOM_SIZE = len(_minimal_part10_dicom_bytes(1))


def _read_explicit_vr_part10(
    path: str, *, stop_before_pixels: bool, force: bool
) -> SimpleNamespace:
    """Dependency adapter that parses the actual Part-10 bytes used by the test."""

    if force is not False or type(stop_before_pixels) is not bool:
        raise ValueError("synthetic Part-10 reads must remain strict")
    payload = Path(path).read_bytes()
    if len(payload) < 132 or payload[128:132] != b"DICM":
        raise ValueError("synthetic fixture is not a Part-10 object")
    offset = 132
    values: dict[tuple[int, int], tuple[str, bytes]] = {}
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValueError("truncated explicit-VR element")
        group, element = struct.unpack_from("<HH", payload, offset)
        vr = payload[offset + 4 : offset + 6].decode("ascii")
        if vr in _LONG_VALUE_REPRESENTATIONS:
            if payload[offset + 6 : offset + 8] != b"\0\0" or offset + 12 > len(
                payload
            ):
                raise ValueError("invalid long explicit-VR element")
            length = struct.unpack_from("<I", payload, offset + 8)[0]
            value_offset = offset + 12
        else:
            length = struct.unpack_from("<H", payload, offset + 6)[0]
            value_offset = offset + 8
        end = value_offset + length
        if length % 2 or end > len(payload):
            raise ValueError("invalid explicit-VR element length")
        if (group, element) == (0x7FE0, 0x0010) and stop_before_pixels:
            break
        values[(group, element)] = (vr, payload[value_offset:end])
        offset = end
    if (0x0002, 0x0010) not in values:
        raise ValueError("Part-10 transfer syntax is missing")

    def text(tag: tuple[int, int]) -> str:
        return values[tag][1].rstrip(b"\0 ").decode("ascii")

    def unsigned_short(tag: tuple[int, int]) -> int:
        vr, value = values[tag]
        if vr != "US" or len(value) != 2:
            raise ValueError("expected a US element")
        return int(struct.unpack("<H", value)[0])

    dataset = SimpleNamespace(
        NumberOfFrames=int(text((0x0028, 0x0008))),
        Rows=unsigned_short((0x0028, 0x0010)),
        Columns=unsigned_short((0x0028, 0x0011)),
        SamplesPerPixel=unsigned_short((0x0028, 0x0002)),
        BitsAllocated=unsigned_short((0x0028, 0x0100)),
        BitsStored=unsigned_short((0x0028, 0x0101)),
        PhotometricInterpretation=text((0x0028, 0x0004)),
        file_meta=SimpleNamespace(
            TransferSyntaxUID=text((0x0002, 0x0010))
        ),
    )
    if not stop_before_pixels:
        vr, pixel_data = values[(0x7FE0, 0x0010)]
        if vr != "OB":
            raise ValueError("synthetic fixture Pixel Data is not OB")
        expected = (
            dataset.NumberOfFrames
            * dataset.Rows
            * dataset.Columns
            * dataset.SamplesPerPixel
        )
        if len(pixel_data) != expected:
            raise ValueError("synthetic fixture Pixel Data length is invalid")
        dataset.PixelData = pixel_data
    return dataset


def _synthetic_pydicom_module() -> ModuleType:
    module = ModuleType("pydicom")
    module.read_calls = []  # type: ignore[attr-defined]

    def dcmread(path: str, *, stop_before_pixels: bool, force: bool) -> Any:
        module.read_calls.append((Path(path).name, stop_before_pixels, force))  # type: ignore[attr-defined]
        return _read_explicit_vr_part10(
            path, stop_before_pixels=stop_before_pixels, force=force
        )

    module.dcmread = dcmread  # type: ignore[attr-defined]
    module.pixels = _PixelsAPI()  # type: ignore[attr-defined]
    return module


def _binary_morphology(
    value: np.ndarray, *, iterations: int, operation: str
) -> np.ndarray:
    result = np.asarray(value, dtype=np.uint8)
    for _ in range(iterations):
        padded = np.pad(result, 1, mode="constant")
        neighborhoods = np.stack(
            [
                padded[y : y + result.shape[0], x : x + result.shape[1]]
                for y in range(3)
                for x in range(3)
            ]
        )
        result = (
            neighborhoods.min(axis=0)
            if operation == "erode"
            else neighborhoods.max(axis=0)
        ).astype(np.uint8)
    return result


def _synthetic_cv2_module() -> ModuleType:
    module = ModuleType("cv2")
    module.INTER_CUBIC = 2  # type: ignore[attr-defined]
    module.RETR_TREE = 3  # type: ignore[attr-defined]
    module.CHAIN_APPROX_SIMPLE = 2  # type: ignore[attr-defined]
    module.setNumThreads = lambda threads: None  # type: ignore[attr-defined]
    module.erode = (  # type: ignore[attr-defined]
        lambda value, kernel, iterations: _binary_morphology(
            value, iterations=iterations, operation="erode"
        )
    )
    module.dilate = (  # type: ignore[attr-defined]
        lambda value, kernel, iterations: _binary_morphology(
            value, iterations=iterations, operation="dilate"
        )
    )

    def flood_fill(
        image: np.ndarray,
        _mask: Any,
        seed_point: tuple[int, int],
        replacement: int,
    ) -> tuple[int, np.ndarray, None, tuple[int, int, int, int]]:
        x_seed, y_seed = seed_point
        target = int(image[y_seed, x_seed])
        if target == replacement:
            return 0, image, None, (x_seed, y_seed, 1, 1)
        queue = deque([(y_seed, x_seed)])
        image[y_seed, x_seed] = replacement
        count = 0
        while queue:
            y_value, x_value = queue.popleft()
            count += 1
            for y_next, x_next in (
                (y_value - 1, x_value),
                (y_value + 1, x_value),
                (y_value, x_value - 1),
                (y_value, x_value + 1),
            ):
                if (
                    0 <= y_next < image.shape[0]
                    and 0 <= x_next < image.shape[1]
                    and int(image[y_next, x_next]) == target
                ):
                    image[y_next, x_next] = replacement
                    queue.append((y_next, x_next))
        return count, image, None, (0, 0, image.shape[1], image.shape[0])

    def resize(
        value: np.ndarray, dimensions: tuple[int, int], *, interpolation: int
    ) -> np.ndarray:
        if interpolation != module.INTER_CUBIC:  # type: ignore[attr-defined]
            raise ValueError("unexpected synthetic interpolation policy")
        width, height = dimensions
        y_indices = np.linspace(0, value.shape[0] - 1, height).astype(np.int64)
        x_indices = np.linspace(0, value.shape[1] - 1, width).astype(np.int64)
        return np.ascontiguousarray(value[y_indices][:, x_indices])

    module.floodFill = flood_fill  # type: ignore[attr-defined]
    module.findContours = lambda value, mode, method: ([], None)  # type: ignore[attr-defined]
    module.convexHull = lambda contour: contour  # type: ignore[attr-defined]
    module.drawContours = lambda image, contours, index, color, thickness: image  # type: ignore[attr-defined]
    module.resize = resize  # type: ignore[attr-defined]
    return module


@contextmanager
def _installed_dependency_modules(
    modules: Mapping[str, ModuleType],
) -> Iterator[None]:
    absent = object()
    previous = {name: sys.modules.get(name, absent) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, prior in previous.items():
            if prior is absent:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior  # type: ignore[assignment]


def _expect_error(
    expected_code: str,
    operation: Callable[[], object],
    expected_type: type[BaseException] | tuple[type[BaseException], ...],
) -> BaseException:
    try:
        operation()
    except expected_type as exc:
        assert getattr(exc, "code", str(exc)) == expected_code
        return exc
    raise AssertionError(f"expected {expected_code}")


def _candidate(index: int) -> dict[str, Any]:
    return {
        "study_id": str(200_000 + index),
        "subject_id": str(100_000 + index),
        "split": "train",
        "expected_object_count": 2,
        "expected_byte_total": 2 * SYNTHETIC_DICOM_SIZE,
        "known_no_cine": False,
        "prior_reconstruction_smoke": False,
    }


def _source_object(
    selected: manifest_contract.SelectedStudy, ordinal: int
) -> dict[str, Any]:
    relative = (
        f"files/p{int(selected.subject_id) // 1_000_000:02d}/p{selected.subject_id}/"
        f"s{selected.study_id}/cine_{ordinal:03d}.dcm"
    )
    payload = _minimal_part10_dicom_bytes(ordinal)
    return {
        "subject_id": selected.subject_id,
        "study_id": selected.study_id,
        "split": "train",
        "source_object_key": hashlib.sha256(
            f"{manifest_contract.SOURCE_RELEASE}\0{relative}".encode("utf-8")
        ).hexdigest(),
        "source_relative_path": relative,
        "size_bytes": len(payload),
        "generation": str(10_000 + ordinal),
        "md5_base64": base64.b64encode(
            hashlib.md5(payload, usedforsecurity=False).digest()
        ).decode("ascii"),
        "crc32c_base64": orchestration_core._crc32c_base64(payload),
    }


def _sealed_exact_five_manifest() -> dict[str, Any]:
    selected = manifest_contract.select_exact_five(
        [_candidate(index) for index in range(1, 6)]
    )
    objects = [
        _source_object(study, ordinal)
        for study in selected
        for ordinal in (1, 2)
    ]
    return manifest_contract.build_sealed_manifest(
        selected_studies=selected,
        source_objects=objects,
        source_authority_commit=COMMIT,
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        source_configuration_hashes=CONFIGURATION_HASHES,
    )


def _bound_scheduler_plan(sealed: dict[str, Any]) -> dict[str, Any]:
    template = scheduler.load_scheduler_plan(
        ROOT / "configs/lvef_c3_canary_scheduler_plan_v1.json"
    )
    return scheduler.bind_scheduler_plan(
        template, sealed["manifest_sha256"], repository_root=ROOT
    )


class _PixelsAPI:
    """Minimal pydicom-3 raw-pixel API over parsed Part-10 Pixel Data."""

    def pixel_array(self, dataset: Any, **kwargs: Any) -> np.ndarray:
        assert kwargs == {"raw": True}
        return np.frombuffer(dataset.PixelData, dtype=np.uint8).reshape(
            dataset.NumberOfFrames,
            dataset.Rows,
            dataset.Columns,
            dataset.SamplesPerPixel,
        ).copy()


class _ArrayTensor:
    """Small numpy adapter sufficient to execute the production transform."""

    def __init__(self, value: Any):
        self.value = np.asarray(value, dtype=np.float32)

    def permute(self, *axes: int) -> _ArrayTensor:
        return _ArrayTensor(self.value.transpose(axes))

    def reshape(self, *shape: int) -> _ArrayTensor:
        return _ArrayTensor(self.value.reshape(shape))

    def sub(self, other: _ArrayTensor) -> _ArrayTensor:
        return _ArrayTensor(self.value - other.value)

    def div(self, other: _ArrayTensor) -> _ArrayTensor:
        return _ArrayTensor(self.value / other.value)

    def __getitem__(self, item: Any) -> _ArrayTensor:
        return _ArrayTensor(self.value[item])

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(self.value.shape)


class _ArrayTorch:
    float32 = np.float32

    @staticmethod
    def as_tensor(value: Any, dtype: Any = None) -> _ArrayTensor:
        assert dtype is np.float32
        return _ArrayTensor(value)


def _manifest_objects(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **source_object,
            "subject_id": study["subject_id"],
            "study_id": study["study_id"],
        }
        for study in envelope["manifest"]["studies"]
        for source_object in study["objects"]
    ]


def _write_csv_exact(
    path: Path, header: tuple[str, ...] | list[str], rows: list[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header), extrasaction="raise")
        writer.writeheader()
        writer.writerows(
            [{key: row.get(key, "") for key in header} for row in rows]
        )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _transition_ledger(
    ledger: Mapping[str, Any], target: str, output_sha256: str
) -> dict[str, Any]:
    batch = ledger["batches"][integration.SYNTHETIC_BATCH_ID]
    predecessor = (
        batch["events"][-1]["receipt_sha256"]
        if batch["events"]
        else ledger["authority"]["batch_plan_sha256"]
    )
    return orchestration_core.apply_transition(
        ledger,
        {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": integration.SYNTHETIC_ATTEMPT_ID,
            "batch_id": integration.SYNTHETIC_BATCH_ID,
            "from_state": batch["state"],
            "to_state": target,
            "status": "PASS",
            "authority": ledger["authority"],
            "input_receipt_sha256": [predecessor],
            "output_manifest_sha256": output_sha256,
        },
    )


def _materialize_production_preservation_inputs(
    context: integration.CanaryIntegrationContext,
) -> dict[str, Any]:
    """Write a complete synthetic instance of the production batch contract."""

    production_root = Path(context.artifacts["synthetic_workspace"].name) / "production"
    production_root.mkdir(mode=0o700)
    attempt_root = production_root / "attempts" / integration.SYNTHETIC_ATTEMPT_ID
    raw_root = attempt_root / "raw" / integration.SYNTHETIC_BATCH_ID
    extraction_root = (
        attempt_root
        / "extracted_cache"
        / integration.SYNTHETIC_BATCH_ID
        / "dicom_extraction"
    )
    batch_root = attempt_root / "batches" / integration.SYNTHETIC_BATCH_ID
    echoprime_root = batch_root / "echoprime"
    objects_root = raw_root / "objects"
    objects_root.mkdir(parents=True)
    extraction_root.mkdir(parents=True)
    echoprime_root.mkdir(parents=True)

    plan = context.artifacts["batch_plan"]
    plan_path = production_root / "synthetic_exact_five_plan.restricted.json"
    _write_json(plan_path, plan)
    environment_path = production_root / "synthetic_environment.restricted.json"
    environment_path.write_bytes(
        integration.synthetic_environment_receipt_bytes(COMMIT)
    )
    checkpoint_path = production_root / "synthetic_checkpoint.bin"
    checkpoint_path.write_bytes(integration.SYNTHETIC_CHECKPOINT_BYTES)

    transferred_by_key = {
        row["source_object_key"]: row
        for row in context.artifacts["transferred_objects"]
    }
    download_rows: list[dict[str, Any]] = []
    for planned in plan["batches"][0]["objects"]:
        transferred = transferred_by_key[planned["source_object_key"]]
        payload = (
            context.artifacts["download_root"]
            / transferred["source_relative_path"]
        ).read_bytes()
        (objects_root / f"{planned['source_object_key']}.dcm").write_bytes(payload)
        download_rows.append(
            {
                "subject_id": planned["subject_id"],
                "study_id": planned["study_id"],
                "source_relative_path": planned["source_relative_path"],
                "download_ok": "true",
                "observed_sha256": hashlib.sha256(payload).hexdigest(),
                "physical_source_key": planned["source_object_key"],
            }
        )
    download_manifest = raw_root / "verified_download_manifest.restricted.csv"
    _write_csv_exact(
        download_manifest,
        [
            "subject_id",
            "study_id",
            "source_relative_path",
            "download_ok",
            "observed_sha256",
            "physical_source_key",
        ],
        download_rows,
    )

    for extracted in context.artifacts["extraction_rows"]:
        source = context.artifacts["extraction_root"] / extracted["output_relative_path"]
        target = extraction_root / extracted["output_relative_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    dicom_rows = list(context.artifacts["dicom_rows"])
    extraction_rows = list(context.artifacts["extraction_rows"])
    _write_csv_exact(
        extraction_root / "dicom_audit.restricted.csv",
        preservation.DICOM_AUDIT_HEADER,
        dicom_rows,
    )
    _write_csv_exact(
        extraction_root / "extraction_manifest.restricted.csv",
        preservation.EXTRACTION_MANIFEST_HEADER,
        extraction_rows,
    )
    dicom_semantics = production_stages.validate_production_dicom_rows(
        dicom_rows, expected_objects=10, expected_studies=5
    )
    extraction_semantics = production_stages.validate_production_extraction_rows(
        extraction_rows, expected_cines=10
    )
    _write_json(
        extraction_root / "dicom_extraction.summary.json",
        {
            "schema_version": 1,
            "artifact_type": "lvef_c3_batch_dicom_extraction_summary_v1",
            "status": "PASS_DICOM_EXTRACTION",
            **dicom_semantics,
            **extraction_semantics,
            "identifiers_emitted": False,
            "paths_emitted": False,
        },
    )

    clip_rows = list(context.artifacts["clip_rows"])
    study_rows = list(context.artifacts["study_rows"])
    dispositions = [
        {
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "disposition": "IMAGING_ELIGIBLE",
        }
        for row in plan["batches"][0]["studies"]
    ]
    _write_csv_exact(
        echoprime_root / "clip_manifest.restricted.csv",
        preservation.CLIP_MANIFEST_HEADER,
        clip_rows,
    )
    _write_csv_exact(
        echoprime_root / "study_manifest.restricted.csv",
        preservation.STUDY_MANIFEST_HEADER,
        study_rows,
    )
    _write_csv_exact(
        echoprime_root / "study_disposition.restricted.csv",
        preservation.DISPOSITION_HEADER,
        dispositions,
    )
    np.savez(
        echoprime_root / "clip_embeddings.restricted.npz",
        embeddings=context.artifacts["clip_embeddings"],
    )
    np.savez(
        echoprime_root / "study_embeddings.restricted.npz",
        embeddings=context.artifacts["study_embeddings"],
    )
    _write_json(
        echoprime_root / "echoprime_pooling.summary.json",
        {
            "schema_version": 1,
            "artifact_type": "lvef_c3_batch_echoprime_pooling_summary_v1",
            "status": "PASS_ECHOPRIME_AND_POOLING",
            "n_clip_embeddings": 10,
            "n_pooled_studies": 5,
            "n_no_cine_studies": 0,
            "embedding_dimension": 512,
            "embedding_dtype": "float32",
            "all_finite": True,
            "encoder_only": True,
            "view_classifier_used": False,
            "pooling": "stable_clip_key_order_float64_mean_then_float32",
            "checkpoint_sha256": hashlib.sha256(
                integration.SYNTHETIC_CHECKPOINT_BYTES
            ).hexdigest(),
            "identifiers_emitted": False,
            "paths_emitted": False,
        },
    )

    runtime_authority = {
        **plan["authority"],
        "batch_plan_sha256": context.artifacts["batch_plan_sha256"],
    }
    ledger = orchestration_core.initialize_resume_ledger(
        plan,
        requirements=integration.canary_requirements(context.manifest),
        attempt_id=integration.SYNTHETIC_ATTEMPT_ID,
        authority=runtime_authority,
        batch_ids=[integration.SYNTHETIC_BATCH_ID],
    )
    ledger = _transition_ledger(ledger, "DOWNLOAD_IN_PROGRESS", "6" * 64)
    for row in plan["batches"][0]["objects"]:
        key = row["source_object_key"]
        ledger = orchestration_core.register_download_attempt(
            ledger,
            batch_id=integration.SYNTHETIC_BATCH_ID,
            source_object_key=key,
            maximum_attempts=1,
        )
        ledger = orchestration_core.mark_download_verified(
            ledger,
            batch_id=integration.SYNTHETIC_BATCH_ID,
            source_object_key=key,
            verification_receipt_sha256=hashlib.sha256(
                f"verification:{key}".encode("ascii")
            ).hexdigest(),
        )
    ledger = orchestration_core.mark_download_manifest(
        ledger,
        batch_id=integration.SYNTHETIC_BATCH_ID,
        manifest_sha256=preservation.sha256_file(download_manifest),
    )
    for state, artifact in (
        ("DOWNLOAD_VERIFIED", download_manifest),
        ("DICOM_AUDIT_COMPLETE", extraction_root / "dicom_audit.restricted.csv"),
        ("EXTRACTION_COMPLETE", extraction_root / "extraction_manifest.restricted.csv"),
        ("EMBEDDING_COMPLETE", echoprime_root / "clip_embeddings.restricted.npz"),
        ("STUDY_POOLING_COMPLETE", echoprime_root / "study_embeddings.restricted.npz"),
    ):
        ledger = _transition_ledger(ledger, state, preservation.sha256_file(artifact))
    ledger_path = batch_root / "download_resume_ledger.restricted.json"
    _write_json(ledger_path, ledger)
    return {
        "production_root": production_root,
        "plan_path": plan_path,
        "environment_path": environment_path,
        "checkpoint_path": checkpoint_path,
        "ledger_path": ledger_path,
        "runtime_authority": runtime_authority,
        "output_root": batch_root / "preservation",
    }


def _synthetic_hooks(
    calls: dict[str, int] | None = None,
) -> integration.CanaryIntegrationHooks:
    counts = calls if calls is not None else {}

    def called(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    def source_transfer(context: integration.CanaryIntegrationContext) -> None:
        called("source_transfer")
        declared = _manifest_objects(context.manifest)
        workspace = tempfile.TemporaryDirectory(prefix="lvef-c3-synthetic-part10-")
        download_root = Path(workspace.name) / "download"
        extraction_root = Path(workspace.name) / "extraction"
        download_root.mkdir(mode=0o700)
        extraction_root.mkdir(mode=0o700)
        transferred: list[dict[str, Any]] = []
        for item in declared:
            scoped = dict(context.access_declared_object(item["source_object_key"]))
            ordinal = int(Path(scoped["source_relative_path"]).stem.rsplit("_", 1)[1])
            payload = _minimal_part10_dicom_bytes(ordinal)
            output = download_root / scoped["source_relative_path"]
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
            scoped["observed_sha256"] = hashlib.sha256(payload).hexdigest()
            transferred.append(scoped)
        context.artifacts.update(
            {
                "synthetic_workspace": workspace,
                "download_root": download_root,
                "extraction_root": extraction_root,
                "synthetic_pydicom": _synthetic_pydicom_module(),
                "synthetic_cv2": _synthetic_cv2_module(),
                "transferred_objects": transferred,
            }
        )

    def integrity_verification(
        context: integration.CanaryIntegrationContext,
    ) -> None:
        called("integrity_verification")
        transferred = context.artifacts["transferred_objects"]
        assert len(transferred) == context.manifest["manifest"][
            "expected_object_count"
        ]
        assert {row["source_object_key"] for row in transferred} == {
            row["source_object_key"] for row in _manifest_objects(context.manifest)
        }
        download_root = context.artifacts["download_root"]
        for row in transferred:
            payload = (download_root / row["source_relative_path"]).read_bytes()
            assert payload[128:132] == b"DICM"
            assert len(payload) == row["size_bytes"] == SYNTHETIC_DICOM_SIZE
            assert hashlib.sha256(payload).hexdigest() == row["observed_sha256"]
            assert base64.b64encode(
                hashlib.md5(payload, usedforsecurity=False).digest()
            ).decode("ascii") == row["md5_base64"]
            assert orchestration_core._crc32c_base64(payload) == row["crc32c_base64"]
        context.artifacts["integrity_verified"] = True

    def dicom_audit_decode(context: integration.CanaryIntegrationContext) -> None:
        called("dicom_audit_decode")
        assert context.artifacts["integrity_verified"] is True
        rows: list[dict[str, Any]] = []
        pydicom_module = context.artifacts["synthetic_pydicom"]
        download_root = context.artifacts["download_root"]
        with _installed_dependency_modules({"pydicom": pydicom_module}):
            for source_object in context.artifacts["transferred_objects"]:
                record = {
                    **source_object,
                    "smoke_role": "production_selected",
                    "download_sha256": source_object["observed_sha256"],
                }
                header = reconstruction._dicom_header_row(
                    record, str(download_root)
                )
                assert header["read_ok"] is True
                assert header["is_multiframe"] is True
                assert header["number_of_frames"] == 4
                assert header["rows"] == header["columns"] == 224
                assert header["samples_per_pixel"] == 3
                assert header["bits_allocated"] == header["bits_stored"] == 8
                assert header["photometric_interpretation"] == "RGB"
                assert header["transfer_syntax_uid"] == "1.2.840.10008.1.2.1"
                dataset = pydicom_module.dcmread(
                    str(download_root / source_object["source_relative_path"]),
                    stop_before_pixels=False,
                    force=False,
                )
                pixels, metadata = reconstruction._normalize_dicom_pixels(
                    dataset, pydicom_module
                )
                assert pixels.shape == (4, 224, 224, 3)
                assert pixels.dtype == np.uint8
                assert metadata["decoder_backend"] == "pydicom_pixels_raw:native"
                rows.append({**header, "pixel_decode_ok": True})
        context.call_trace.extend(
            ["dicom_header_row", "normalize_dicom_pixels", "part10_pixel_decode"]
        )
        validation = production_stages.validate_production_dicom_rows(
            rows,
            expected_objects=len(rows),
            expected_studies=manifest_contract.EXACT_STUDIES,
        )
        context.call_trace.append("validate_production_dicom_rows")
        assert validation["n_pixel_decode_failures"] == 0
        context.artifacts["dicom_rows"] = rows

    def cine_extraction(context: integration.CanaryIntegrationContext) -> None:
        called("cine_extraction")
        extraction_rows: list[dict[str, Any]] = []
        extracted: dict[str, np.ndarray] = {}
        by_locator = {
            row["source_relative_path"]: row
            for row in context.artifacts["transferred_objects"]
        }
        modules = {
            "pydicom": context.artifacts["synthetic_pydicom"],
            "cv2": context.artifacts["synthetic_cv2"],
        }
        download_root = context.artifacts["download_root"]
        extraction_root = context.artifacts["extraction_root"]
        with _installed_dependency_modules(modules):
            for dicom_row in context.artifacts["dicom_rows"]:
                source_object = by_locator[dicom_row["source_relative_path"]]
                extracted_row = reconstruction._extract_one(
                    dicom_row, str(download_root), str(extraction_root)
                )
                assert extracted_row["write_ok"] is True, extracted_row["error_code"]
                assert extracted_row["error_code"] is None
                assert extracted_row["mask_status"] == "APPLIED"
                output_path = extraction_root / extracted_row["output_relative_path"]
                with np.load(output_path, allow_pickle=False) as archive:
                    assert set(archive.files) == {
                        "frames",
                        "sampled_indices",
                        "source_num_frames",
                    }
                    sampled = archive["frames"]
                    sampled_indices = archive["sampled_indices"]
                    assert sampled.shape == reconstruction.EXTRACTION_SHAPE
                    assert sampled.dtype == np.uint8
                    assert sampled_indices.tolist() == [0, 1, 2, 3] + [3] * 28
                    extracted[extracted_row["clip_key"]] = sampled.copy()
                extraction_rows.append(
                    {
                        **extracted_row,
                        "physical_source_key": source_object["source_object_key"],
                        "pixel_decode_ok": True,
                    }
                )
        assert len(context.artifacts["synthetic_pydicom"].read_calls) == 30
        context.call_trace.extend(["extract_one", "temporal_sample"])
        validation = production_stages.validate_production_extraction_rows(
            extraction_rows, expected_cines=len(extraction_rows)
        )
        context.call_trace.append("validate_production_extraction_rows")
        assert validation["all_shapes_and_dtypes_valid"] is True
        context.artifacts["extraction_rows"] = extraction_rows
        context.artifacts["extracted_frames"] = extracted

    def encoder_inference(context: integration.CanaryIntegrationContext) -> None:
        called("encoder_inference")
        vectors: list[np.ndarray] = []
        clip_rows: list[dict[str, Any]] = []
        for index, row in enumerate(context.artifacts["extraction_rows"]):
            encoder_input = reconstruction._prepare_encoder_input(
                context.artifacts["extracted_frames"][row["clip_key"]], _ArrayTorch
            )
            if "prepare_encoder_input" not in context.call_trace:
                context.call_trace.append("prepare_encoder_input")
            assert encoder_input.shape == (3, 16, 224, 224)
            vector = (
                np.arange(reconstruction.EMBEDDING_WIDTH, dtype=np.float32)
                + np.float32(index + 1)
            ) / np.float32(1000.0)
            vectors.append(vector)
            clip_rows.append(
                {
                    "embedding_idx": str(index),
                    "subject_id": row["subject_id"],
                    "study_id": row["study_id"],
                    "clip_key": row["clip_key"],
                    "physical_source_key": row["physical_source_key"],
                    "embedding_l2_norm": repr(
                        float(np.linalg.norm(vector.astype(np.float64)))
                    ),
                    "embedding_sha256": reconstruction.array_content_sha256(vector),
                    "write_ok": True,
                }
            )
        clip_embeddings = np.stack(vectors).astype(np.float32)
        validation = production_stages.validate_embedding_values(
            clip_embeddings.tolist(), expected_rows=len(clip_embeddings)
        )
        assert validation == {
            "n_embeddings": 10,
            "embedding_dimension": 512,
            "embedding_dtype": "float32",
            "all_finite": True,
        }
        context.artifacts["clip_embeddings"] = clip_embeddings
        context.artifacts["clip_rows"] = clip_rows

    def preserve(context: integration.CanaryIntegrationContext) -> None:
        called("preservation")
        study_embeddings = context.artifacts["study_embeddings"]
        assert study_embeddings.shape == (5, 512)
        assert study_embeddings.dtype == np.float32
        assert context.artifacts["pooling_validation"] == {
            "eligible_studies": 5,
            "no_cine_studies": 0,
            "one_vector_per_eligible_study": True,
        }
        authorities = _materialize_production_preservation_inputs(context)
        context.artifacts["preservation_receipt"] = preservation.preserve_batch(
            contract_path=integration.DEFAULT_ORCHESTRATION_CONTRACT,
            plan_path=authorities["plan_path"],
            batch_id=integration.SYNTHETIC_BATCH_ID,
            attempt_id=integration.SYNTHETIC_ATTEMPT_ID,
            governing_commit=COMMIT,
            production_root=authorities["production_root"],
            output_root=authorities["output_root"],
            environment_receipt=authorities["environment_path"],
            checkpoint=authorities["checkpoint_path"],
            scheduler_job_identity="synthetic-canary-job",
            input_ledger=authorities["ledger_path"],
            requirements=integration.canary_requirements(context.manifest),
            expected_runtime_authority=authorities["runtime_authority"],
            scheduler_runner_path=ROOT / "scripts/scc_run_lvef_c3_canary.sh",
        )
        context.call_trace.append("preserve_batch")
        assert set(context.artifacts["preservation_receipt"]) == (
            finalizer.PRESERVATION_ELIGIBILITY_RECEIPT_KEYS
        )
        context.artifacts["synthetic_workspace"].cleanup()

    return integration.CanaryIntegrationHooks(
        source_transfer=source_transfer,
        integrity_verification=integrity_verification,
        dicom_audit_decode=dicom_audit_decode,
        cine_extraction=cine_extraction,
        encoder_inference=encoder_inference,
        preservation=preserve,
    )


def test_exact_five_canary_end_to_end_reuses_production_functions() -> None:
    sealed = _sealed_exact_five_manifest()
    plan = _bound_scheduler_plan(sealed)
    calls: dict[str, int] = {}
    assert sealed["manifest"]["expected_object_count"] == 10
    assert sealed["manifest"]["expected_byte_total"] == 10 * SYNTHETIC_DICOM_SIZE

    result = integration.run_synthetic_integration(
        execution_state_path=ROOT / "configs/lvef_c3_execution_state_v1.yaml",
        manifest=sealed,
        scheduler_plan=plan,
        hooks=_synthetic_hooks(calls),
    )

    assert calls == {
        "source_transfer": 1,
        "integrity_verification": 1,
        "dicom_audit_decode": 1,
        "cine_extraction": 1,
        "encoder_inference": 1,
        "preservation": 1,
    }
    assert result.completed_stages == scheduler.ORDERED_STAGE_IDS
    assert result.claim_ledger["status"] == "COMPLETE"
    assert result.claim_ledger["submission_count"] == 5
    assert result.claim_ledger["production_continuation_triggered"] is False
    assert result.study_embeddings.shape == (5, 512)
    assert result.study_embeddings.dtype == np.float32
    assert np.isfinite(result.study_embeddings).all()
    assert result.aggregate_summary["status"] == (
        "PASS_CANARY_PRESERVATION_FINALIZED_RETAINED_CACHE"
    )
    assert result.aggregate_summary["successful_train_studies"] == 5
    assert result.aggregate_summary["verified_source_objects"] == 10
    assert result.aggregate_summary["selected_source_bytes"] == (
        10 * SYNTHETIC_DICOM_SIZE
    )
    assert result.aggregate_summary["pooled_studies"] == 5
    assert result.aggregate_summary["identifiers_emitted"] is False
    assert result.aggregate_summary["restricted_paths_emitted"] is False
    assert result.production_continuation is False
    assert result.reachable_roles == frozenset(scheduler.ORDERED_STAGE_IDS)
    assert result.reachable_roles.isdisjoint(FORBIDDEN_ROLES)

    identities = integration.PRODUCTION_FUNCTIONS
    expected_identities = {
        "source_transfer": orchestration_core.execute_exact_batch_download,
        "download_integrity": orchestration_core.verify_downloaded_partial,
        "dicom_stage": production_stages.run_production_dicom_extraction,
        "dicom_rows": production_stages.validate_production_dicom_rows,
        "extraction_rows": production_stages.validate_production_extraction_rows,
        "echoprime_stage": production_stages.run_production_echoprime,
        "embedding_values": production_stages.validate_embedding_values,
        "temporal_sampling": reconstruction.temporal_sample,
        "encoder_input": reconstruction._prepare_encoder_input,
        "study_mean_pooling": preservation.mean_pool_study_embeddings,
        "pooling_records": preservation.validate_study_pooling_records,
        "preservation": preservation.preserve_batch,
        "canary_finalization": finalizer.finalize_canary_preservation_receipt,
    }
    assert set(identities) == set(expected_identities)
    assert all(
        identities[name] is production_function
        for name, production_function in expected_identities.items()
    )
    assert {"_dicom_header_row", "_extract_one"}.issubset(
        set(production_stages.run_production_dicom_extraction.__code__.co_names)
    )
    assert "mean_pool_study_embeddings" in set(
        production_stages.run_production_echoprime.__code__.co_names
    )
    assert set(result.call_trace) >= {
        "validate_execution_state",
        "validate_manifest",
        "validate_scheduler_plan",
        "dicom_header_row",
        "normalize_dicom_pixels",
        "part10_pixel_decode",
        "validate_production_dicom_rows",
        "extract_one",
        "validate_production_extraction_rows",
        "temporal_sample",
        "prepare_encoder_input",
        "validate_embedding_values",
        "mean_pool_study_embeddings",
        "validate_study_pooling_records",
        "preserve_batch",
        "finalize_canary_preservation_receipt",
    }

    # Keep every fail-closed proof in this same decisive integration test.
    # These helpers intentionally do not become independently collected tests.
    _assert_scope_and_seal_fail_closed_before_any_processing_hook()
    _assert_undeclared_access_and_stage_failure_block_successors_without_retry()
    _assert_frozen_dag_rejects_dynamic_overflow_duplicate_and_forbidden_roles()


def _assert_scope_and_seal_fail_closed_before_any_processing_hook() -> None:
    sealed = _sealed_exact_five_manifest()
    body = sealed["manifest"]

    four = copy.deepcopy(body)
    four["studies"].pop()
    _expect_error(
        "CANARY_EXACT_FIVE_COUNT_INVALID",
        lambda: manifest_contract.seal_manifest(four),
        manifest_contract.CanaryManifestError,
    )
    six = copy.deepcopy(body)
    six["studies"].append(copy.deepcopy(six["studies"][-1]))
    _expect_error(
        "CANARY_EXACT_FIVE_COUNT_INVALID",
        lambda: manifest_contract.seal_manifest(six),
        manifest_contract.CanaryManifestError,
    )
    for split in ("val", "test"):
        nontrain = copy.deepcopy(body)
        nontrain["studies"][0]["split"] = split
        _expect_error(
            "CANARY_SPLIT_NOT_TRAIN",
            lambda nontrain=nontrain: manifest_contract.seal_manifest(nontrain),
            manifest_contract.CanaryManifestError,
        )
    duplicate_study = copy.deepcopy(body)
    duplicate_study["studies"][1]["study_id"] = duplicate_study["studies"][0][
        "study_id"
    ]
    _expect_error(
        "CANARY_STUDY_DUPLICATE",
        lambda: manifest_contract.seal_manifest(duplicate_study),
        manifest_contract.CanaryManifestError,
    )
    duplicate_subject = copy.deepcopy(body)
    duplicate_subject["studies"][1]["subject_id"] = duplicate_subject["studies"][
        0
    ]["subject_id"]
    _expect_error(
        "CANARY_SUBJECT_DUPLICATE",
        lambda: manifest_contract.seal_manifest(duplicate_subject),
        manifest_contract.CanaryManifestError,
    )

    object_breach = [_candidate(index) for index in range(1, 6)]
    for candidate in object_breach:
        candidate["expected_object_count"] = 151
    _expect_error(
        "CANARY_OBJECT_CEILING_EXCEEDED",
        lambda: manifest_contract.select_exact_five(object_breach),
        manifest_contract.CanaryManifestError,
    )
    byte_breach = [_candidate(index) for index in range(1, 6)]
    for candidate in byte_breach:
        candidate["expected_byte_total"] = 1_000_000_001
    _expect_error(
        "CANARY_BYTE_CEILING_EXCEEDED",
        lambda: manifest_contract.select_exact_five(byte_breach),
        manifest_contract.CanaryManifestError,
    )

    altered = copy.deepcopy(sealed)
    altered["manifest"]["source_manifest_sha256"] = "e" * 64
    calls: dict[str, int] = {}
    _expect_error(
        "CANARY_MANIFEST_SEAL_MISMATCH",
        lambda: integration.run_synthetic_integration(
            execution_state_path=ROOT / "configs/lvef_c3_execution_state_v1.yaml",
            manifest=altered,
            scheduler_plan=_bound_scheduler_plan(sealed),
            hooks=_synthetic_hooks(calls),
        ),
        manifest_contract.CanaryManifestError,
    )
    assert calls == {}


def _assert_undeclared_access_and_stage_failure_block_successors_without_retry() -> None:
    sealed = _sealed_exact_five_manifest()
    plan = _bound_scheduler_plan(sealed)

    normal = _synthetic_hooks()

    def undeclared(context: integration.CanaryIntegrationContext) -> None:
        context.access_declared_object("f" * 64)

    undeclared_hooks = integration.CanaryIntegrationHooks(
        source_transfer=undeclared,
        integrity_verification=normal.integrity_verification,
        dicom_audit_decode=normal.dicom_audit_decode,
        cine_extraction=normal.cine_extraction,
        encoder_inference=normal.encoder_inference,
        preservation=normal.preservation,
    )
    undeclared_error = _expect_error(
        "CANARY_UNDECLARED_OBJECT_ACCESS",
        lambda: integration.run_synthetic_integration(
            execution_state_path=ROOT / "configs/lvef_c3_execution_state_v1.yaml",
            manifest=sealed,
            scheduler_plan=plan,
            hooks=undeclared_hooks,
        ),
        integration.CanaryIntegrationError,
    )
    undeclared_ledger = undeclared_error.claim_ledger
    assert undeclared_ledger["status"] == "FAILED"
    assert undeclared_ledger["submission_count"] == 1
    assert undeclared_ledger["failed_stage_id"] == "DOWNLOAD"

    calls: dict[str, int] = {}
    baseline = _synthetic_hooks(calls)

    def fail_integrity(context: integration.CanaryIntegrationContext) -> None:
        calls["integrity_verification"] = calls.get("integrity_verification", 0) + 1
        raise RuntimeError("synthetic integrity failure")

    failed_hooks = integration.CanaryIntegrationHooks(
        source_transfer=baseline.source_transfer,
        integrity_verification=fail_integrity,
        dicom_audit_decode=baseline.dicom_audit_decode,
        cine_extraction=baseline.cine_extraction,
        encoder_inference=baseline.encoder_inference,
        preservation=baseline.preservation,
    )
    failure = _expect_error(
        "CANARY_STAGE_FAILED",
        lambda: integration.run_synthetic_integration(
            execution_state_path=ROOT / "configs/lvef_c3_execution_state_v1.yaml",
            manifest=sealed,
            scheduler_plan=plan,
            hooks=failed_hooks,
        ),
        integration.CanaryIntegrationError,
    )
    ledger = failure.claim_ledger
    assert calls == {"source_transfer": 1, "integrity_verification": 1}
    assert ledger["status"] == "FAILED"
    assert ledger["submission_count"] == 1
    assert ledger["failed_stage_id"] == "DOWNLOAD"
    assert plan["stage_retry_count"] == 0
    assert plan["automatic_resubmission_permitted"] is False
    _expect_error(
        "CANARY_SCHEDULER_SUCCESSOR_AFTER_FAILURE",
        lambda: scheduler.claim_stage_submission(
            ledger,
            plan,
            "DICOM_EXTRACTION",
            predecessor_receipt_sha256s=["1" * 64],
        ),
        scheduler.CanarySchedulerPlanError,
    )
    _expect_error(
        "CANARY_SCHEDULER_SUCCESSOR_AFTER_FAILURE",
        lambda: scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD"),
        scheduler.CanarySchedulerPlanError,
    )


def _assert_frozen_dag_rejects_dynamic_overflow_duplicate_and_forbidden_roles() -> None:
    sealed = _sealed_exact_five_manifest()
    plan = _bound_scheduler_plan(sealed)
    ledger = scheduler.initialize_claim_ledger(plan)

    _expect_error(
        "CANARY_SCHEDULER_UNDECLARED_STAGE",
        lambda: scheduler.claim_stage_submission(ledger, plan, "MODEL_FITTING"),
        scheduler.CanarySchedulerPlanError,
    )
    ledger = scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD")
    receipt = hashlib.sha256(b"DOWNLOAD").hexdigest()
    ledger = scheduler.record_stage_result(
        ledger, plan, "DOWNLOAD", passed=True, result_receipt_sha256=receipt
    )
    _expect_error(
        "CANARY_SCHEDULER_DUPLICATE_STAGE_SUBMISSION",
        lambda: scheduler.claim_stage_submission(ledger, plan, "DOWNLOAD"),
        scheduler.CanarySchedulerPlanError,
    )
    _expect_error(
        "CANARY_SCHEDULER_DYNAMIC_OR_OUT_OF_ORDER_STAGE",
        lambda: scheduler.claim_stage_submission(
            ledger,
            plan,
            "ECHOPRIME_EMBEDDING",
            predecessor_receipt_sha256s=[receipt],
        ),
        scheduler.CanarySchedulerPlanError,
    )
    predecessor = receipt
    for stage_id in scheduler.ORDERED_STAGE_IDS[1:]:
        ledger = scheduler.claim_stage_submission(
            ledger,
            plan,
            stage_id,
            predecessor_receipt_sha256s=[predecessor],
        )
        predecessor = hashlib.sha256(stage_id.encode("ascii")).hexdigest()
        ledger = scheduler.record_stage_result(
            ledger,
            plan,
            stage_id,
            passed=True,
            result_receipt_sha256=predecessor,
        )
    assert ledger["submission_count"] == plan["scheduler_submission_count"] == 5
    _expect_error(
        "CANARY_SCHEDULER_SUBMISSION_LIMIT_EXCEEDED",
        lambda: scheduler.claim_stage_submission(
            ledger,
            plan,
            "CANARY_FINALIZATION",
            predecessor_receipt_sha256s=[predecessor],
        ),
        scheduler.CanarySchedulerPlanError,
    )

    dynamic_plan = copy.deepcopy(plan)
    dynamic_plan["ordered_stage_ids"].append("MODEL_FITTING")
    dynamic_plan["stages"].append(copy.deepcopy(dynamic_plan["stages"][-1]))
    dynamic_plan["scheduler_submission_count"] = 6
    _expect_error(
        "CANARY_SCHEDULER_TOPOLOGY_INVALID",
        lambda: scheduler.validate_scheduler_plan(dynamic_plan),
        scheduler.CanarySchedulerPlanError,
    )

    hook_fields = {item.name for item in fields(integration.CanaryIntegrationHooks)}
    assert hook_fields == {
        "source_transfer",
        "integrity_verification",
        "dicom_audit_decode",
        "cine_extraction",
        "encoder_inference",
        "preservation",
    }
    assert hook_fields.isdisjoint(
        {"model_fitting", "endpoint_prediction", "confirmatory_performance"}
    )
    assert plan["production_continuation"] is False
    assert plan["array_expansion_permitted"] is False
    assert plan["gpu_stage_count"] == 1
