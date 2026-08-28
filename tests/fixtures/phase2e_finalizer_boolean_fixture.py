from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def compare_nonimage_table(*args, **kwargs):
    old = np.bool_(True)
    new = np.bool_(True)
    return float(new - old)


def write_completion_certificate(
    original_csv: Path,
    corrected_csv: Path,
    output_json: Path,
) -> None:
    frame = compare_nonimage_table(
        original_csv,
        corrected_csv,
        "metrics",
        ("target", "baseline_tier", "split", "model"),
    )
    exact = bool(frame.loc[frame["metric"].eq("input_changed"), "exact_match"].iloc[0])
    output_json.write_text(
        json.dumps(
            {
                "status": "FIXTURE_CERTIFICATE_COMPLETE" if exact else "BLOCKED",
                "boolean_exact_match": exact,
            }
        ),
        encoding="utf-8",
    )
