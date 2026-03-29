#!/usr/bin/env python3
"""Diagnose view classification by comparing DICOM-direct vs NPZ-cached processing.

Saves side-by-side sample frames and runs the view classifier both ways
to identify whether the misclassification comes from preprocessing or
a genuine domain shift in the classifier.

Usage:
  python scripts/diagnose_view_classification.py \
    --extraction-manifest outputs/cloud_cohorts/stage_d_500study_scc/extract_smoke/extraction_manifest.csv \
    --embedding-manifest outputs/cloud_cohorts/stage_d_500study_scc/echoprime_embeddings/clip_embedding_manifest.csv \
    --download-root /restricted/projectnb/mimicecho/echo_ai_data/cloud_cohorts/stage_d_500study_scc \
    --weights-dir /restricted/project/mimicecho/echoprime_weights \
    --output-dir outputs/diagnostics/view_classification \
    --n-samples 20 \
    --device auto
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pydicom
import torch
import torchvision

COARSE_VIEWS = [
    "A2C", "A3C", "A4C", "A5C", "Apical_Doppler",
    "Doppler_Parasternal_Long", "Doppler_Parasternal_Short",
    "Parasternal_Long", "Parasternal_Short", "SSN", "Subcostal",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--extraction-manifest", type=Path, required=True)
    p.add_argument("--embedding-manifest", type=Path, required=True)
    p.add_argument("--download-root", type=Path, required=True)
    p.add_argument("--weights-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--n-samples", type=int, default=20)
    p.add_argument("--device", default="auto")
    return p.parse_args()


def choose_device(requested: str) -> torch.device:
    if requested == "cuda" or (requested == "auto" and torch.cuda.is_available()):
        return torch.device("cuda")
    return torch.device("cpu")


def crop_and_scale(img, size=224, zoom=0.1):
    res = (size, size)
    in_res = (img.shape[1], img.shape[0])
    r_in = in_res[0] / in_res[1]
    r_out = 1.0
    if r_in > r_out:
        pad = int(round((in_res[0] - r_out * in_res[1]) / 2))
        img = img[:, pad:-pad]
    elif r_in < r_out:
        pad = int(round((in_res[1] - in_res[0] / r_out) / 2))
        img = img[pad:-pad]
    if zoom != 0:
        px = round(int(img.shape[1] * zoom))
        py = round(int(img.shape[0] * zoom))
        if px > 0 and py > 0:
            img = img[py:-py, px:-px]
    return cv2.resize(img, res, interpolation=cv2.INTER_CUBIC)


def mask_outside_ultrasound(original_pixels: np.ndarray) -> np.ndarray:
    """Exact copy of the EchoPrime/mve-echo masking function."""
    if original_pixels.ndim != 4 or original_pixels.shape[-1] != 3:
        return original_pixels
    vid = np.copy(original_pixels)
    try:
        frame_sum = vid[0].astype(np.float32)
        frame_sum = cv2.cvtColor(frame_sum, cv2.COLOR_YUV2RGB)
        frame_sum = cv2.cvtColor(frame_sum, cv2.COLOR_RGB2GRAY)
        frame_sum = np.where(frame_sum > 0, 1, 0)
        for i in range(vid.shape[0]):
            f = vid[i].astype(np.uint8)
            f = cv2.cvtColor(f, cv2.COLOR_YUV2RGB)
            f = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY)
            f = np.where(f > 0, 1, 0)
            frame_sum = np.add(frame_sum, f)
        kernel = np.ones((3, 3), np.uint8)
        frame_sum = cv2.erode(np.uint8(frame_sum), kernel, iterations=10)
        frame_sum = np.where(frame_sum > 0, 1, 0)
        f0 = cv2.cvtColor(vid[0].astype(np.uint8), cv2.COLOR_YUV2RGB)
        f0 = cv2.cvtColor(f0, cv2.COLOR_RGB2GRAY)
        fl = cv2.cvtColor(vid[-1].astype(np.uint8), cv2.COLOR_YUV2RGB)
        fl = cv2.cvtColor(fl, cv2.COLOR_RGB2GRAY)
        fd = abs(np.subtract(f0, fl))
        fd = np.where(fd > 0, 1, 0)
        fd[0:20, 0:20] = 0
        fo = np.add(frame_sum, fd)
        fo = np.where(fo > 1, 1, 0)
        fo = cv2.dilate(np.uint8(fo), kernel, iterations=10).astype(np.uint8)
        cv2.floodFill(fo, None, (0, 0), 100)
        fo = np.where(fo != 100, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(fo, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            hull = cv2.convexHull(c)
            cv2.drawContours(fo, [hull], -1, (255, 0, 0), 3)
        fo = np.where(fo > 0, 1, 0).astype(np.uint8)
        cv2.floodFill(fo, None, (0, 0), 100)
        mask = np.array(np.where(fo != 100, 255, 0), dtype=bool)
        for i in range(len(vid)):
            frame = vid[i].astype(np.uint8)
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR)
            frame = cv2.bitwise_and(frame, frame, mask=mask.astype(np.uint8))
            vid[i] = frame
        return vid
    except Exception:
        return vid


def process_dicom_direct(dicom_path: Path, n_frames=32, stride=2, size=224):
    """Process a DICOM exactly like mve-echo / EchoPrime: raw pixels → tensor."""
    dcm = pydicom.dcmread(str(dicom_path))
    pixels = dcm.pixel_array
    photometric = getattr(dcm, "PhotometricInterpretation", "UNKNOWN")

    if pixels.ndim == 3 and pixels.shape[-1] != 3:
        pixels = np.repeat(pixels[..., None], 3, axis=3)
    elif pixels.ndim == 2 or (pixels.ndim == 3 and pixels.shape[-1] == 3):
        return None, {"skip": True, "reason": "still image", "photometric": photometric}

    raw_first_frame = pixels[0].copy()
    pixels = mask_outside_ultrasound(pixels)
    masked_first_frame = pixels[0].copy()

    x = np.zeros((len(pixels), size, size, 3))
    for i in range(len(x)):
        x[i] = crop_and_scale(pixels[i], size=size)

    cropped_first_frame = x[0].copy()
    x = torch.as_tensor(x, dtype=torch.float).permute(3, 0, 1, 2)
    mean = torch.tensor([29.110628, 28.076836, 29.096405]).reshape(3, 1, 1, 1)
    std = torch.tensor([47.989223, 46.456997, 47.20083]).reshape(3, 1, 1, 1)
    x.sub_(mean).div_(std)

    if x.shape[1] < n_frames:
        pad = torch.zeros((3, n_frames - x.shape[1], size, size), dtype=torch.float)
        x = torch.cat((x, pad), dim=1)
    x = x[:, 0:n_frames:stride, :, :]

    info = {
        "photometric": photometric,
        "original_shape": list(dcm.pixel_array.shape),
        "raw_first_frame": raw_first_frame,
        "masked_first_frame": masked_first_frame,
        "cropped_first_frame": cropped_first_frame,
    }
    return x.unsqueeze(0), info


def process_from_npz(npz_path: Path):
    """Process a clip from our NPZ cache, matching extract_echoprime_embeddings.py."""
    with np.load(npz_path) as data:
        frames = data["frames"]

    npz_first_frame = frames[0].copy()
    x = torch.as_tensor(frames, dtype=torch.float32).permute(3, 0, 1, 2)
    mean = torch.tensor([29.110628, 28.076836, 29.096405]).reshape(3, 1, 1, 1)
    std = torch.tensor([47.989223, 46.456997, 47.20083]).reshape(3, 1, 1, 1)
    x = x.sub(mean).div(std)
    target_frames = 32
    if x.shape[1] < target_frames:
        pad = torch.zeros((3, target_frames - x.shape[1], 224, 224), dtype=torch.float32)
        x = torch.cat([x, pad], dim=1)
    else:
        x = x[:, :target_frames, :, :]
    x = x[:, 0:target_frames:2, :, :]
    return x.unsqueeze(0), npz_first_frame


def classify_view(view_model, tensor, device):
    first_frame = tensor[:, :, 0, :, :].to(device)
    with torch.no_grad():
        logits = view_model(first_frame)
        probs = torch.softmax(logits, dim=1)
        pred_id = torch.argmax(logits, dim=1).item()
        confidence = probs[0, pred_id].item()
    top3_ids = torch.topk(probs, k=3, dim=1).indices[0].tolist()
    top3 = [(COARSE_VIEWS[i], round(probs[0, i].item(), 4)) for i in top3_ids]
    return COARSE_VIEWS[pred_id], confidence, top3


def main():
    args = parse_args()
    device = choose_device(args.device)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    vc_state = torch.load(str(args.weights_dir / "view_classifier.pt"), map_location="cpu")
    view_model = torchvision.models.convnext_base()
    view_model.classifier[-1] = torch.nn.Linear(view_model.classifier[-1].in_features, 11)
    view_model.load_state_dict(vc_state)
    view_model.eval().to(device)

    ext_df = pd.read_csv(args.extraction_manifest)
    emb_df = pd.read_csv(args.embedding_manifest)
    ext_ok = ext_df[ext_df["write_ok"].fillna(False)].copy()

    sample = ext_ok.sample(n=min(args.n_samples, len(ext_ok)), random_state=42)
    results = []

    for idx, (_, row) in enumerate(sample.iterrows()):
        dicom_rel = str(row["dicom_filepath"]).lstrip("/")
        dicom_path = args.download_root / dicom_rel
        npz_path = Path(row["output_path"])
        study_id = int(row["study_id"])

        emb_row = emb_df[
            (emb_df["study_id"] == study_id) &
            (emb_df["dicom_filepath"] == dicom_rel)
        ]
        cached_view = emb_row["view_name"].values[0] if len(emb_row) > 0 else "N/A"

        entry = {
            "idx": idx,
            "study_id": study_id,
            "dicom_filepath": dicom_rel,
            "npz_path": str(npz_path),
            "dicom_exists": dicom_path.exists(),
            "npz_exists": npz_path.exists(),
            "cached_view": cached_view,
        }

        # --- Method A: Direct from DICOM (mve-echo style) ---
        if dicom_path.exists():
            try:
                tensor_a, info = process_dicom_direct(dicom_path)
                if tensor_a is not None:
                    view_a, conf_a, top3_a = classify_view(view_model, tensor_a, device)
                    entry["direct_view"] = view_a
                    entry["direct_confidence"] = round(conf_a, 4)
                    entry["direct_top3"] = top3_a
                    entry["photometric"] = info["photometric"]
                    entry["original_shape"] = info["original_shape"]

                    for label, img in [
                        ("raw", info["raw_first_frame"]),
                        ("masked", info["masked_first_frame"]),
                        ("cropped", info["cropped_first_frame"]),
                    ]:
                        if img is not None:
                            out = args.output_dir / f"sample_{idx:02d}_{label}.png"
                            if img.dtype != np.uint8:
                                img = np.clip(img, 0, 255).astype(np.uint8)
                            cv2.imwrite(str(out), img)
                else:
                    entry["direct_view"] = "SKIP"
                    entry["direct_confidence"] = 0
                    entry.update(info)
            except Exception as e:
                entry["direct_error"] = str(e)

        # --- Method B: From NPZ cache (our pipeline) ---
        if npz_path.exists():
            try:
                tensor_b, npz_frame = process_from_npz(npz_path)
                view_b, conf_b, top3_b = classify_view(view_model, tensor_b, device)
                entry["npz_view"] = view_b
                entry["npz_confidence"] = round(conf_b, 4)
                entry["npz_top3"] = top3_b

                out = args.output_dir / f"sample_{idx:02d}_npz.png"
                cv2.imwrite(str(out), npz_frame.astype(np.uint8))
            except Exception as e:
                entry["npz_error"] = str(e)

        entry["views_match"] = entry.get("direct_view") == entry.get("npz_view")
        results.append(entry)
        print(f"[{idx+1}/{len(sample)}] study={study_id} "
              f"direct={entry.get('direct_view','?')} "
              f"npz={entry.get('npz_view','?')} "
              f"cached={cached_view} "
              f"match={entry.get('views_match','?')} "
              f"photo={entry.get('photometric','?')}")

    report_path = args.output_dir / "view_diagnosis_report.json"
    report_path.write_text(json.dumps(results, indent=2, default=str))

    n_match = sum(1 for r in results if r.get("views_match"))
    n_total = sum(1 for r in results if "direct_view" in r and "npz_view" in r)
    view_dist_direct = {}
    view_dist_npz = {}
    for r in results:
        v = r.get("direct_view")
        if v:
            view_dist_direct[v] = view_dist_direct.get(v, 0) + 1
        v = r.get("npz_view")
        if v:
            view_dist_npz[v] = view_dist_npz.get(v, 0) + 1

    photometrics = {}
    for r in results:
        p = r.get("photometric", "?")
        photometrics[p] = photometrics.get(p, 0) + 1

    summary = {
        "n_samples": len(results),
        "n_compared": n_total,
        "n_match": n_match,
        "match_rate": round(n_match / n_total, 3) if n_total > 0 else None,
        "view_distribution_direct": view_dist_direct,
        "view_distribution_npz": view_dist_npz,
        "photometric_interpretations": photometrics,
    }
    summary_path = args.output_dir / "view_diagnosis_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2))
    print(f"\n[written] {report_path}")
    print(f"[written] {summary_path}")
    print(f"[written] Sample frames in {args.output_dir}/")


if __name__ == "__main__":
    main()
