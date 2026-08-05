from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "scc_run_lvef_metadata_adjudication.sh"
RUNBOOK = ROOT / "docs" / "lvef_multitask" / "scc_phase1ebc_commands.md"


def test_metadata_wrapper_is_fail_closed_and_uses_resolved_python() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "umask 077" in text
    assert "stat -c '%a'" in text
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"' in text
    assert 'test -z "$(git status --porcelain)"' in text
    assert "audit_lvef_multitask_technical_metadata.py" in text
    assert '"${RAW_CANONICAL_MAPPING:?}"' in text
    assert '"${EXPECTED_RAW_CANONICAL_MAPPING_SHA256:?}"' in text
    assert '--raw-canonical-mapping-csv "$RAW_CANONICAL_MAPPING"' in text
    assert (
        '--expected-raw-canonical-mapping-sha256 '
        '"$EXPECTED_RAW_CANONICAL_MAPPING_SHA256"'
    ) in text
    assert '--input "$RAW_CANONICAL_MAPPING"' in text
    assert "build_lvef_clinician_signoff_packet.py build" in text
    assert "lvef_multitask_analysis_modes.py" in text
    assert "confirmatory_performance_accessed\":false" in text
    assert 'gate["full_mapping_universe_completeness_proven"] is True' in text
    assert 'authority["exact_40_counts_reconciled"] is True' in text
    assert not re.search(r"(^|[;&|]\s*)(python|python3)\s+[^\n]*\.py", text, re.MULTILINE)


def test_metadata_wrapper_does_not_reference_predictive_workflows() -> None:
    text = WRAPPER.read_text(encoding="utf-8").lower()
    for prohibited in (
        "run_multimodal_fusion.py",
        "run_tabular_measurement_baseline.py",
        "run_multitask_vision_baseline.py",
        "run_multitask_tabular_baseline.py",
        "run_multitask_fusion_baseline.py",
    ):
        assert prohibited not in text


def test_metadata_runbook_binds_historical_authority_hashes() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert 'scripts/resolve_lvef_scc_python.sh --record-json "$PYTHON_RECORD"' in text
    assert 'PYTHON_RECORD_PARENT="/restricted/projectnb/mimicecho/audits/' in text
    assert "test ! -e \"$PYTHON_RECORD\"" in text
    assert "printf 'PYTHON_RECORD=%q\\n' \"$PYTHON_RECORD\"" in text
    assert 'PYTHON="$(scripts/resolve_lvef_scc_python.sh)"' not in text
    assert (
        'EXPECTED_STRUCTURED_MEASUREMENTS_SHA256="'
        '95fc852457c25ca548d6fa1ae3ec5d2740b99a6aa3d53297a5b424ffc3d27023"'
    ) in text
    assert (
        'EXPECTED_RAW_CANONICAL_MAPPING_SHA256="'
        '2f1b6c424c62c39017396130fe074accce06cbb05e1977b4c27144e0f824fd18"'
    ) in text
    assert 'EXPECTED_STRUCTURED_MEASUREMENTS_SHA256="$(sha256sum' not in text
