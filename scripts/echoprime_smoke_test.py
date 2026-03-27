#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

import torch
import torchvision


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
        # pragma: no cover
    if requested != "auto":
        raise RuntimeError(f"Unknown device request: {requested}")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def read_jsonable_exception(exc: Exception) -> dict[str, Any]:
    return {
        "type": exc.__class__.__name__,
        "message": str(exc),
    }


def get_memory_snapshot(device: torch.device) -> dict[str, Any]:
    out: dict[str, Any] = {"device": str(device)}
    if device.type == "cuda":
        out["allocated_bytes"] = int(torch.cuda.memory_allocated(device))
        out["max_allocated_bytes"] = int(torch.cuda.max_memory_allocated(device))
    elif device.type == "mps" and hasattr(torch, "mps"):
        current = getattr(torch.mps, "current_allocated_memory", None)
        driver = getattr(torch.mps, "driver_allocated_memory", None)
        if current is not None:
            out["allocated_bytes"] = int(current())
        if driver is not None:
            out["driver_allocated_bytes"] = int(driver())
    else:
        out["allocated_bytes"] = None
    return out


def verify_paths(repo_root: Path) -> dict[str, Any]:
    assets = {
        "repo_root": repo_root,
        "weights_dir": repo_root / "model_data" / "weights",
        "candidates_dir": repo_root / "model_data" / "candidates_data",
        "encoder_weights": repo_root / "model_data" / "weights" / "echo_prime_encoder.pt",
        "view_classifier_weights": repo_root / "model_data" / "weights" / "view_classifier.pt",
        "text_encoder_weights": repo_root / "model_data" / "weights" / "echo_prime_text_encoder.pt",
        "candidate_embeddings_p1": repo_root / "model_data" / "candidates_data" / "candidate_embeddings_p1.pt",
        "candidate_embeddings_p2": repo_root / "model_data" / "candidates_data" / "candidate_embeddings_p2.pt",
        "candidate_studies": repo_root / "model_data" / "candidates_data" / "candidate_studies.csv",
        "candidate_reports": repo_root / "model_data" / "candidates_data" / "candidate_reports.pkl",
        "candidate_labels": repo_root / "model_data" / "candidates_data" / "candidate_labels.pkl",
        "mil_weights": repo_root / "assets" / "MIL_weights.csv",
        "per_section": repo_root / "assets" / "per_section.json",
        "section_to_phenotypes": repo_root / "assets" / "section_to_phenotypes.pkl",
    }
    results = {}
    for key, path in assets.items():
        results[key] = {
            "path": str(path),
            "exists": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        }
    return results


def run_encoder_probe(repo_root: Path, device: torch.device) -> dict[str, Any]:
    encoder_path = repo_root / "model_data" / "weights" / "echo_prime_encoder.pt"
    view_path = repo_root / "model_data" / "weights" / "view_classifier.pt"
    if not encoder_path.exists():
        raise FileNotFoundError(f"Missing encoder weights: {encoder_path}")
    if not view_path.exists():
        raise FileNotFoundError(f"Missing view-classifier weights: {view_path}")

    results: dict[str, Any] = {"device": str(device)}

    start = time.perf_counter()
    checkpoint = torch.load(encoder_path, map_location="cpu")
    model = torchvision.models.video.mvit_v2_s()
    model.head[-1] = torch.nn.Linear(model.head[-1].in_features, 512)
    model.load_state_dict(checkpoint)
    model.eval().to(device)
    x = torch.zeros(1, 3, 16, 224, 224, device=device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    with torch.no_grad():
        y = model(x)
    results["video_embedding_shape"] = list(y.shape)
    results["video_forward_seconds"] = round(time.perf_counter() - start, 3)
    results["video_memory"] = get_memory_snapshot(device)

    start = time.perf_counter()
    checkpoint = torch.load(view_path, map_location="cpu")
    view_model = torchvision.models.convnext_base()
    view_model.classifier[-1] = torch.nn.Linear(view_model.classifier[-1].in_features, 11)
    view_model.load_state_dict(checkpoint)
    view_model.eval().to(device)
    first_frames = torch.zeros(1, 3, 224, 224, device=device)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    with torch.no_grad():
        logits = view_model(first_frames)
    results["view_logits_shape"] = list(logits.shape)
    results["view_forward_seconds"] = round(time.perf_counter() - start, 3)
    results["view_memory"] = get_memory_snapshot(device)

    return results


def run_full_probe(repo_root: Path, device: torch.device) -> dict[str, Any]:
    if device.type == "mps":
        raise RuntimeError(
            "Full EchoPrime repo object does not support MPS as released; it hard-codes CUDA-or-CPU."
        )

    os.chdir(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from echo_prime import EchoPrime  # local import to keep repo-root path assumptions explicit

    start = time.perf_counter()
    ep = EchoPrime(device=str(device))
    init_seconds = round(time.perf_counter() - start, 3)

    x = torch.zeros(4, 3, 16, 224, 224)
    start = time.perf_counter()
    encoded = ep.encode_study(x)
    encode_seconds = round(time.perf_counter() - start, 3)

    start = time.perf_counter()
    preds = ep.predict_metrics(encoded, k=5)
    predict_seconds = round(time.perf_counter() - start, 3)

    return {
        "device": str(device),
        "init_seconds": init_seconds,
        "encoded_shape": list(encoded.shape),
        "encode_seconds": encode_seconds,
        "predict_seconds": predict_seconds,
        "n_pred_metrics": len(preds),
        "sample_metric_keys": sorted(list(preds.keys()))[:10],
        "memory": get_memory_snapshot(device),
    }


def troubleshooting_lines() -> list[str]:
    return [
        "Run this script from a venv with torch, torchvision, pandas, scikit-learn, pydicom, opencv-python-headless, tqdm, and transformers installed.",
        "If import fails outside the repo root, keep using this script because it changes into the repo root before importing EchoPrime.",
        "If MPS is available but full EchoPrime fails, that is expected: the released EchoPrime class does not use MPS.",
        "If full inference fails, verify that model_data.zip is unzipped under EchoPrime/model_data and that both candidate embedding shards were moved into model_data/candidates_data.",
        "If Hugging Face downloads fail later, note that the main EchoPrime inference path does not need the text encoder for encoder-only smoke testing.",
        "If memory pressure is high, run encoder-only first and defer full retrieval-bank loading until you have the release assets on a larger machine.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test EchoPrime assets and a minimal forward pass.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "EchoPrime",
        help="Path to the EchoPrime repo root.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda", "mps"],
        help="Execution device for encoder probe.",
    )
    parser.add_argument(
        "--skip-full",
        action="store_true",
        help="Skip full EchoPrime object initialization even if candidate assets exist.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs" / "echoprime_smoke_test.json",
        help="Path to write structured results.",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    result: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo_root": str(repo_root),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
        },
        "torch": {
            "version": torch.__version__,
            "torchvision": torchvision.__version__,
            "cuda_available": torch.cuda.is_available(),
            "mps_available": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
            "mps_built": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_built()),
        },
        "assets": verify_paths(repo_root),
        "troubleshooting": troubleshooting_lines(),
    }

    try:
        device = choose_device(args.device)
        result["requested_device"] = args.device
        result["resolved_device"] = str(device)
        result["encoder_probe"] = run_encoder_probe(repo_root, device)
    except Exception as exc:
        result["encoder_probe_error"] = read_jsonable_exception(exc)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        return 1

    full_asset_keys = [
        "candidate_embeddings_p1",
        "candidate_embeddings_p2",
        "candidate_studies",
        "candidate_reports",
        "candidate_labels",
        "mil_weights",
        "per_section",
        "section_to_phenotypes",
        "view_classifier_weights",
    ]
    has_full_assets = all(result["assets"][key]["exists"] for key in full_asset_keys)
    result["has_full_assets"] = has_full_assets

    if not args.skip_full and has_full_assets:
        try:
            full_device = choose_device("cuda" if torch.cuda.is_available() else "cpu")
            result["full_probe"] = run_full_probe(repo_root, full_device)
        except Exception as exc:
            result["full_probe_error"] = read_jsonable_exception(exc)
    else:
        result["full_probe_skipped"] = True

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
