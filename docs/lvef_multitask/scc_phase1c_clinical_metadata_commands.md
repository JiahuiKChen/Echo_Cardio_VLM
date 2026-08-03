# SCC Phase 1C clinical metadata commands — superseded

The original Phase 1C block is retained in Git history but is intentionally no longer runnable from this document. It used an environment-dependent bare interpreter and, when sourced under `set -e`, its Python syntax failure terminated the interactive SCC shell before a clinical metadata packet was created.

Do not remove `from __future__ import annotations` or use an older interpreter. Use the authoritative Python resolver and isolated Bash-block subprocess workflow in `scc_phase1d_execution_commands.md`. That workflow requires Python 3.10 or newer, validates NumPy, pandas, SciPy, scikit-learn, and PyYAML before packet creation, keeps audit stdout/stderr restricted, and exposes a nonzero child status without terminating the parent interactive shell.
