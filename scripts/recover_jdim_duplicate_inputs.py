#!/usr/bin/env python3
"""Deferred scheduled verification of recovered JDIM duplicate inputs.

This command is intentionally not suitable for a login node: it may decode
DICOM, open NPZ/embedding arrays, hash large files, and run the frozen encoder.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import pandas as pd

from jdim_tier1.duplicate_recovery import (
    BLOCKED_CHECKPOINT_PROVENANCE,
    BLOCKED_PREPROCESSING_PROVENANCE,
    DUPLICATE_SEMANTICS_STILL_UNRESOLVED,
    MODE_DICOM_REHYDRATION,
    MODE_HISTORICAL_NPZ,
    RecoveryPrerequisites,
    WindowSpec,
    prerequisite_status,
    verify_processed_input,
    write_recovery_outputs,
)
from jdim_tier1.safety import Tier1BlockedError, sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-manifest-csv", type=Path, required=True)
    parser.add_argument("--historical-embedding-npz", type=Path, required=True)
    parser.add_argument("--weights-dir", type=Path, required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--extraction-script", type=Path, required=True)
    parser.add_argument("--extraction-script-sha256", required=True)
    parser.add_argument("--embedding-script", type=Path, required=True)
    parser.add_argument("--embedding-script-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def _require_columns(frame: pd.DataFrame, columns: set[str]) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing:
        raise ValueError(f"recovery manifest missing columns: {missing}")


def _parse_json_list(value: Any, label: str) -> list[Any]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError(f"{label} must be a JSON list")
    return parsed


def _window(row: pd.Series) -> WindowSpec:
    def optional_int(column: str) -> int | None:
        value = row.get(column)
        if value is None or pd.isna(value) or str(value).strip() == "":
            return None
        return int(value)

    return WindowSpec(
        window_index=optional_int("window_index"),
        frame_start=optional_int("frame_start"),
        frame_end=optional_int("frame_end"),
    )


def _rehydrate_frames(source: Path) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    import pydicom

    from extract_mimic_echo_cines import (
        crop_and_scale,
        mask_outside_ultrasound,
        normalize_pixels,
        temporal_sample,
    )

    dataset = pydicom.dcmread(str(source), stop_before_pixels=False)
    raw = normalize_pixels(dataset)
    if raw.shape[0] <= 1:
        raise ValueError("historical input is not multiframe")
    masked = mask_outside_ultrasound(raw)
    resized = np.stack([crop_and_scale(frame, 224) for frame in masked], axis=0).astype(
        np.uint8
    )
    sampled, indices = temporal_sample(resized, 32)
    metadata = {
        "source_num_frames": int(raw.shape[0]),
        "source_rows": int(raw.shape[1]),
        "source_columns": int(raw.shape[2]),
    }
    return sampled, indices, metadata


def _load_encoder(weights_dir: Path, device_request: str):
    import torch

    from extract_echoprime_embeddings import choose_device, load_models

    device = choose_device(device_request)
    model, _ = load_models(weights_dir, device, encoder_only=True)

    def encode(value: np.ndarray) -> np.ndarray:
        tensor = torch.as_tensor(value, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            output = model(tensor)
        return output.detach().cpu().numpy().astype(np.float32)[0]

    return encode


def main() -> int:
    args = parse_args()
    if args.output_root.exists():
        raise FileExistsError(f"refusing to overwrite output root: {args.output_root}")
    frame = pd.read_csv(args.recovery_manifest_csv)
    _require_columns(
        frame,
        {"group_token", "mode", "embedding_indices_json", "source_candidates_json", "npz_path"},
    )

    extraction_hash = sha256_file(args.extraction_script)
    embedding_hash = sha256_file(args.embedding_script)
    checkpoint_path = args.weights_dir / "echo_prime_encoder.pt"
    checkpoint_hash = sha256_file(checkpoint_path)
    preprocessing_ok = bool(
        extraction_hash == args.extraction_script_sha256
        and embedding_hash == args.embedding_script_sha256
    )
    checkpoint_ok = checkpoint_hash == args.checkpoint_sha256

    if not preprocessing_ok:
        rows = pd.DataFrame(
            [
                {
                    **row.to_dict(),
                    "status": BLOCKED_PREPROCESSING_PROVENANCE,
                    "detail": "extraction or embedding script hash mismatch",
                }
                for _, row in frame.iterrows()
            ]
        )
        write_recovery_outputs(rows, args.output_root, Path(__file__).resolve().parents[1])
        return 2
    if not checkpoint_ok:
        rows = pd.DataFrame(
            [
                {
                    **row.to_dict(),
                    "status": BLOCKED_CHECKPOINT_PROVENANCE,
                    "detail": "encoder checkpoint hash mismatch",
                }
                for _, row in frame.iterrows()
            ]
        )
        write_recovery_outputs(rows, args.output_root, Path(__file__).resolve().parents[1])
        return 2

    with np.load(args.historical_embedding_npz, allow_pickle=False) as archive:
        if "embeddings" not in archive:
            raise ValueError("historical embedding NPZ lacks embeddings")
        historical_embeddings = np.asarray(archive["embeddings"])
    encoder = _load_encoder(args.weights_dir, args.device)

    output_rows: list[dict[str, Any]] = []
    reconstructed_payloads: dict[str, tuple[np.ndarray, np.ndarray, dict[str, int]]] = {}
    with TemporaryDirectory() as tempdir:
        temporary = Path(tempdir)
        for _, row in frame.iterrows():
            mode = str(row["mode"]).strip()
            sources = [Path(value) for value in _parse_json_list(row["source_candidates_json"], "source candidates")]
            npz_path = Path(str(row["npz_path"])) if str(row["npz_path"]).strip() else None
            prerequisites = RecoveryPrerequisites(
                mode=mode,
                source_candidates=sources,
                recovered_npz=npz_path,
                preprocessing_expected_sha256=args.extraction_script_sha256,
                preprocessing_observed_sha256=extraction_hash,
                frame_selection_pinned=True,
                checkpoint_expected_sha256=args.checkpoint_sha256,
                checkpoint_observed_sha256=checkpoint_hash,
            )
            preflight_status, detail = prerequisite_status(prerequisites)
            if preflight_status != "READY":
                output_rows.append(
                    {**row.to_dict(), "status": preflight_status, "detail": detail}
                )
                continue

            indices = [int(value) for value in _parse_json_list(row["embedding_indices_json"], "embedding indices")]
            if not indices or min(indices) < 0 or max(indices) >= len(historical_embeddings):
                output_rows.append(
                    {
                        **row.to_dict(),
                        "status": DUPLICATE_SEMANTICS_STILL_UNRESOLVED,
                        "detail": "historical embedding index is missing or out of bounds",
                    }
                )
                continue
            historical_vectors = [historical_embeddings[index] for index in indices]

            source_sha = ""
            processed_file_sha = ""
            if mode == MODE_HISTORICAL_NPZ:
                assert npz_path is not None
                processed_file_sha = sha256_file(npz_path)
                with np.load(npz_path, allow_pickle=False) as recovered:
                    evidence = verify_processed_input(
                        recovered,
                        historical_vectors,
                        encoder,
                        window=_window(row),
                    )
            elif mode == MODE_DICOM_REHYDRATION:
                available = [path for path in sources if path.is_file()]
                source = available[0]
                source_sha = sha256_file(source)
                sampled, sampled_indices, metadata = _rehydrate_frames(source)
                payload: dict[str, np.ndarray] = {
                    "frames": sampled,
                    "sampled_indices": sampled_indices,
                    **{
                        key: np.asarray([value], dtype=np.int32)
                        for key, value in metadata.items()
                    },
                }
                evidence = verify_processed_input(
                    payload,
                    historical_vectors,
                    encoder,
                    window=_window(row),
                )
                token = str(row["group_token"])
                staged = temporary / f"{token}.npz"
                np.savez_compressed(staged, **payload)
                processed_file_sha = sha256_file(staged)
                reconstructed_payloads[token] = (sampled, sampled_indices, metadata)
            else:
                evidence = None

            if evidence is None:
                output_rows.append(
                    {
                        **row.to_dict(),
                        "status": DUPLICATE_SEMANTICS_STILL_UNRESOLVED,
                        "detail": f"unsupported mode: {mode}",
                    }
                )
                continue
            output_rows.append(
                {
                    **row.to_dict(),
                    **evidence.__dict__,
                    "source_dicom_sha256": source_sha,
                    "processed_npz_sha256": processed_file_sha,
                    "checkpoint_sha256": checkpoint_hash,
                    "extraction_script_sha256": extraction_hash,
                    "embedding_script_sha256": embedding_hash,
                }
            )

        output = pd.DataFrame(output_rows)
        summary = write_recovery_outputs(
            output,
            args.output_root,
            Path(__file__).resolve().parents[1],
        )
        if reconstructed_payloads:
            recovered_dir = args.output_root / "restricted" / "reconstructed_inputs"
            recovered_dir.mkdir()
            for token, (sampled, indices, metadata) in reconstructed_payloads.items():
                np.savez_compressed(
                    recovered_dir / f"{token}.npz",
                    frames=sampled,
                    sampled_indices=indices,
                    **{
                        key: np.asarray([value], dtype=np.int32)
                        for key, value in metadata.items()
                    },
                )

    print(json.dumps(summary, indent=2))
    return 0 if summary["n_still_unresolved"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
