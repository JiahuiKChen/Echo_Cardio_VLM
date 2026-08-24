"""JDIM major-revision Tier-1 tooling.

The package operates on synthetic fixtures locally and on restricted frozen
artifacts on SCC. Public helpers never write row-level clinical data unless a
restricted output root outside the Git worktree is explicitly supplied.
"""

PROTOCOL_VERSION = "jdim-tier1-v1"
DEFAULT_SEED = 20260824
DEFAULT_BOOTSTRAP_N = 2000

