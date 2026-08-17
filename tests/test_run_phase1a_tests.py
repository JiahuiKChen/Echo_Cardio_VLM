from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_phase1a_tests as runner


def test_manual_skip_requires_the_current_modules_declared_exception_type() -> None:
    class DeclaredSkip(RuntimeError):
        pass

    class ForeignSkip(RuntimeError):
        pass

    module = ModuleType("synthetic_dependency_light_test")
    module._ManualSkip = DeclaredSkip

    assert runner._is_module_declared_manual_skip(module, DeclaredSkip("cv2"))
    assert not runner._is_module_declared_manual_skip(module, ForeignSkip("cv2"))

    module._ManualSkip = "_ManualSkip"
    assert not runner._is_module_declared_manual_skip(module, DeclaredSkip("cv2"))
