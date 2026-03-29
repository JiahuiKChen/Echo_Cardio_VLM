#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import torchvision


COARSE_VIEWS = [
    "A2C",
    "A3C",
    "A4C",
    "A5C",
    "Apical_Doppler",
    "Doppler_Parasternal_Long",
    "Doppler_Parasternal_Short",
    "Parasternal_Long",
    "Parasternal_Short",
    "SSN",
    "Subcostal",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract EchoPrime-style clip embeddings (512+11) from extracted NPZ cine clips."
    )
    parser.add_argument("--extraction-manifest", type=Path, required=True, help="Path to extraction_manifest.csv")
    parser.add_argument("--weights-dir", type=Path, required=True, help="Path to EchoPrime model_data/weights")
    parser.add_argument("--output-npz", type=Path, required=True, help="Destination NPZ for embeddings matrix")
    parser.add_argument("--output-manifest", type=Path, required=True, help="Destination CSV for per-clip metadata")
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
        help="Optional summary path. Defaults to <output-manifest>.summary.json",
    )
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-clips", type=int, default=0, help="Cap number of clips processed. 0 disables.")
    parser.add_argument("--progress-every", type=int, default=100, help="Print progress every N clips.")
    parser.add_argument("--checkpoint-every", type=int, default=500, help="Save checkpoint every N clips (0 disables).")
    parser.add_argument(
        "--path-prefix-from",
        type=str,
        default=None,
        help="Optional source prefix in extraction manifest output_path entries to rewrite.",
    )
    parser.add_argument(
        "--path-prefix-to",
        type=str,
        default=None,
        help="Optional destination prefix for rewritten output_path entries.",
    )
    parser.add_argument(
        "--encoder-only",
        action="store_true",
        help="Output 512-d encoder features only (skip unreliable view classifier).",
    )
    return parser.parse_args()


def choose_device(requested: str) -> torch.device:
    if requested == "cpu":
        return torch.device("cpu")
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but not available.")
        return torch.device("cuda")
    if requested == "mps":
        if not getattr(torch.backends, "mps", None) or not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but not available.")
        return torch.device("mps")
    if requested != "auto":
        raise RuntimeError(f"Unknown device request: {requested}")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_models(
    weights_dir: Path, device: torch.device, encoder_only: bool = False,
) -> tuple[torch.nn.Module, torch.nn.Module | None]:
    encoder_ckpt = weights_dir / "echo_prime_encoder.pt"
    if not encoder_ckpt.exists():
        raise FileNotFoundError(f"Missing encoder checkpoint: {encoder_ckpt}")

    video_model = torchvision.models.video.mvit_v2_s()
    video_model.head[-1] = torch.nn.Linear(video_model.head[-1].in_features, 512)
    video_state = torch.load(str(encoder_ckpt), map_location="cpu")
    video_model.load_state_dict(video_state)
    video_model.eval().to(device)
    for p in video_model.parameters():
        p.requires_grad = False

    if encoder_only:
        return video_model, None

    view_ckpt = weights_dir / "view_classifier.pt"
    if not view_ckpt.exists():
        raise FileNotFoundError(f"Missing view-classifier checkpoint: {view_ckpt}")
    view_model = torchvision.models.convnext_base()
    view_model.classifier[-1] = torch.nn.Linear(view_model.classifier[-1].in_features, 11)
    view_state = torch.load(str(view_ckpt), map_location="cpu")
    view_model.load_state_dict(view_state)
    view_model.eval().to(device)
    for p in view_model.parameters():
        p.requires_grad = False

    return video_model, view_model


def prepare_clip(frames: np.ndarray, mean: torch.Tensor, std: torch.Tensor) -> torch.Tensor:
    if frames.ndim != 4:
        raise ValueError(f"Expected frames with shape (T,H,W,C), got {frames.shape}")
    if frames.shape[-1] != 3:
        raise ValueError(f"Expected 3 channels, got shape {frames.shape}")

    x = torch.as_tensor(frames, dtype=torch.float32).permute(3, 0, 1, 2)  # C,T,H,W
    if x.shape[2] != 224 or x.shape[3] != 224:
        raise ValueError(f"Expected spatial size 224x224, got {x.shape[2]}x{x.shape[3]}")

    x = x.sub(mean).div(std)
    target_frames = 32
    if x.shape[1] < target_frames:
        pad = torch.zeros((3, target_frames - x.shape[1], 224, 224), dtype=torch.float32)
        x = torch.cat([x, pad], dim=1)
    else:
        x = x[:, :target_frames, :, :]
    x = x[:, 0:target_frames:2, :, :]  # -> C,16,H,W
    if x.shape[1] != 16:
        raise ValueError(f"Expected 16 temporal frames after stride, got {x.shape[1]}")
    return x


def load_clip_tensor(npz_path: Path, mean: torch.Tensor, std: torch.Tensor) -> torch.Tensor:
    with np.load(npz_path) as data:
        if "frames" not in data:
            raise KeyError("NPZ missing 'frames' array")
        frames = data["frames"]
    return prepare_clip(frames=frames, mean=mean, std=std)


def maybe_rewrite_path(raw_path: str, prefix_from: str | None, prefix_to: str | None) -> str:
    if not prefix_from and not prefix_to:
        return raw_path
    if bool(prefix_from) != bool(prefix_to):
        raise ValueError("Both --path-prefix-from and --path-prefix-to must be provided together.")
    assert prefix_from is not None
    assert prefix_to is not None
    if raw_path.startswith(prefix_from):
        return prefix_to + raw_path[len(prefix_from) :]
    return raw_path


def main() -> int:
    args = parse_args()
    if bool(args.path_prefix_from) != bool(args.path_prefix_to):
        raise ValueError("Both --path-prefix-from and --path-prefix-to must be provided together.")

    device = choose_device(args.device)
    start = time.time()

    df = pd.read_csv(args.extraction_manifest)
    if "write_ok" not in df.columns:
        raise ValueError("Extraction manifest missing write_ok column")
    work_df = df[df["write_ok"].fillna(False)].copy()
    work_df = work_df.sort_values(["study_id", "dicom_filepath"]).reset_index(drop=True)
    if args.max_clips > 0:
        work_df = work_df.head(args.max_clips).reset_index(drop=True)
    if work_df.empty:
        raise RuntimeError("No eligible clips in extraction manifest after filtering write_ok=true.")

    encoder_only = args.encoder_only
    emb_dim = 512 if encoder_only else 523
    video_model, view_model = load_models(
        weights_dir=args.weights_dir.resolve(), device=device, encoder_only=encoder_only,
    )
    mean = torch.tensor([29.110628, 28.076836, 29.096405], dtype=torch.float32).reshape(3, 1, 1, 1)
    std = torch.tensor([47.989223, 46.456997, 47.20083], dtype=torch.float32).reshape(3, 1, 1, 1)

    rows: list[dict[str, Any]] = []
    embeddings: list[np.ndarray] = []
    batch_tensors: list[torch.Tensor] = []
    batch_meta: list[dict[str, Any]] = []
    n_failed = 0

    def _run_inference(tensors: list[torch.Tensor], metas: list[dict[str, Any]]) -> None:
        """Run video encoder (and optionally view classifier) on a batch."""
        batch = torch.stack(tensors, dim=0).to(device)
        with torch.no_grad():
            feat = video_model(batch)
            feat_norm = torch.linalg.norm(feat, dim=1).detach().cpu().numpy().astype(np.float32)

            if encoder_only or view_model is None:
                emb_np = feat.detach().cpu().numpy().astype(np.float32)
                view_id_np = np.full(len(metas), -1, dtype=np.int32)
            else:
                first_frames = batch[:, :, 0, :, :]
                logits = view_model(first_frames)
                view_id = torch.argmax(logits, dim=1)
                one_hot = torch.nn.functional.one_hot(view_id, num_classes=11).float()
                emb_np = torch.cat([feat, one_hot], dim=1).detach().cpu().numpy().astype(np.float32)
                view_id_np = view_id.detach().cpu().numpy().astype(np.int32)

        for i, meta in enumerate(metas):
            embedding_idx = len(embeddings)
            embeddings.append(emb_np[i])
            vid = int(view_id_np[i])
            rows.append(
                {
                    "embedding_idx": embedding_idx,
                    "subject_id": int(meta["subject_id"]),
                    "study_id": int(meta["study_id"]),
                    "dicom_filepath": str(meta["dicom_filepath"]),
                    "npz_path": str(meta["npz_path"]),
                    "view_id": vid if vid >= 0 else None,
                    "view_name": COARSE_VIEWS[vid] if 0 <= vid < len(COARSE_VIEWS) else None,
                    "embedding_l2_norm": float(feat_norm[i]),
                    "write_ok": True,
                    "error": None,
                }
            )

    def flush_batch() -> None:
        nonlocal batch_tensors, batch_meta, n_failed
        if not batch_tensors:
            return
        try:
            _run_inference(batch_tensors, batch_meta)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            print(f"[warn] OOM on batch of {len(batch_tensors)} clips, retrying one-at-a-time")
            for t, m in zip(batch_tensors, batch_meta):
                try:
                    _run_inference([t], [m])
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    n_failed += 1
                    rows.append(
                        {
                            "embedding_idx": -1,
                            "subject_id": int(m["subject_id"]),
                            "study_id": int(m["study_id"]),
                            "dicom_filepath": str(m["dicom_filepath"]),
                            "npz_path": str(m["npz_path"]),
                            "view_id": None,
                            "view_name": None,
                            "embedding_l2_norm": None,
                            "write_ok": False,
                            "error": "CUDA OOM even at batch_size=1",
                        }
                    )
                    print(f"[warn] OOM on single clip {m['dicom_filepath']}, marking failed")
        batch_tensors = []
        batch_meta = []

    def save_checkpoint(tag: str = "checkpoint") -> None:
        if not embeddings:
            return
        ckpt_npz = args.output_npz.parent / f"{args.output_npz.stem}_{tag}.npz"
        ckpt_csv = args.output_manifest.parent / f"{args.output_manifest.stem}_{tag}.csv"
        np.savez_compressed(ckpt_npz, embeddings=np.stack(embeddings, axis=0).astype(np.float32))
        pd.DataFrame(rows).to_csv(ckpt_csv, index=False)
        print(f"[checkpoint] saved {len(embeddings)} embeddings -> {ckpt_npz.name}")

    total = len(work_df)
    for i, (_, row) in enumerate(work_df.iterrows(), start=1):
        raw_npz_path = str(row["output_path"])
        rewritten_npz_path = maybe_rewrite_path(raw_npz_path, args.path_prefix_from, args.path_prefix_to)
        npz_path = Path(rewritten_npz_path)
        try:
            clip = load_clip_tensor(npz_path=npz_path, mean=mean, std=std)
            batch_tensors.append(clip)
            batch_meta.append(
                {
                    "subject_id": int(row["subject_id"]),
                    "study_id": int(row["study_id"]),
                    "dicom_filepath": str(row["dicom_filepath"]),
                    "npz_path": str(npz_path.resolve()),
                    "npz_path_source": raw_npz_path,
                }
            )
            if len(batch_tensors) >= args.batch_size:
                flush_batch()
        except Exception as exc:
            n_failed += 1
            rows.append(
                {
                    "embedding_idx": -1,
                    "subject_id": int(row["subject_id"]),
                    "study_id": int(row["study_id"]),
                    "dicom_filepath": str(row["dicom_filepath"]),
                    "npz_path": str(npz_path),
                    "npz_path_source": raw_npz_path,
                    "view_id": None,
                    "view_name": None,
                    "embedding_l2_norm": None,
                    "write_ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if args.progress_every > 0 and i % args.progress_every == 0:
            print(f"[info] processed {i}/{total}")
        if args.checkpoint_every > 0 and i % args.checkpoint_every == 0:
            flush_batch()
            save_checkpoint(tag=f"at{i}")

    flush_batch()

    if embeddings:
        emb_arr = np.stack(embeddings, axis=0).astype(np.float32)
    else:
        emb_arr = np.zeros((0, emb_dim), dtype=np.float32)

    args.output_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output_npz, embeddings=emb_arr)

    out_df = pd.DataFrame(rows)
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.output_manifest, index=False)

    ok_df = out_df[out_df["write_ok"].fillna(False)]
    summary = {
        "requested_rows": int(total),
        "successful_rows": int(len(ok_df)),
        "failed_rows": int(n_failed),
        "n_studies": int(ok_df["study_id"].nunique()) if not ok_df.empty else 0,
        "n_subjects": int(ok_df["subject_id"].nunique()) if not ok_df.empty else 0,
        "embedding_shape": [int(emb_arr.shape[0]), int(emb_arr.shape[1])],
        "device": str(device),
        "batch_size": int(args.batch_size),
        "weights_dir": str(args.weights_dir.resolve()),
        "path_prefix_from": args.path_prefix_from,
        "path_prefix_to": args.path_prefix_to,
        "output_npz": str(args.output_npz.resolve()),
        "output_manifest": str(args.output_manifest.resolve()),
        "elapsed_seconds": round(time.time() - start, 2),
    }
    summary_path = args.summary_json or args.output_manifest.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {args.output_npz.resolve()}")
    print(f"[written] {args.output_manifest.resolve()}")
    print(f"[written] {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
