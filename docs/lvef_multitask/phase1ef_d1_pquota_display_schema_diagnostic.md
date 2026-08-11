# Phase 1E-F-D1 `pquota` display-schema diagnostic

Status: **PASS — read-only diagnostic captured; attempts 001 and 002 remain
immutable; attempt 003 remains unexecuted**.

Diagnostic attempt:
`lvef_multitask_phase1ef_d1_pquota_display_schema_diagnostic_attempt_001`.

## Authority and boundary

The diagnostic ran on SCC at Git authority
`498e0b676ca08e91321743bf856676eb285dae38`. It invoked the pinned,
root-controlled regular executable `/usr/local/etc/quota/pquota` twice under
`LANG=C` and `LC_ALL=C`:

```text
env -i PATH=<fixed-system-path> LANG=C LC_ALL=C pquota -u <project-principal>
```

`COLUMNS` and `LINES` were not supplied. One invocation used redirected,
nonterminal stdout and stderr; the other used a pseudo-terminal. Both exited
0. Raw output and exact command metadata remain in the owner-private restricted
diagnostic root. No
raw table, username, device name, unrelated path, credential, or token entered
Git.

The executable was 5,840 bytes with SHA-256
`d0aacf79af95e8a558e2b27f81b685210427fe773a3aff93b4f1b1ba5ba339ab`,
matching the committed authority. The nonterminal capture was 534 bytes with
SHA-256 `e5e84ac37854fdf08eacc06bf7f3cb22943a8e41e25718f894cefc4d0c517f55`;
stderr was empty. The PTY capture was 543 bytes with SHA-256
`96fe1161fec6e7ef1a357404cd09f9df78ffb7ec5a7d34cbcaf307fde79c6f04`.

## Sanitized structural result

- Raw lines: 9; blank lines: 2; nonblank lines: 7.
- Header: two wrapped lines with the normalized tokens `quota`, `quota`,
  `usage`, `usage`, `project`, `space`, `(gb)`, `(files)`, `(gb)`, `(files)`.
- Exact project rows: two, both at column zero with five fields.
- Backed/research `RPROJECT`-prefix matches: 1/1.
- Obsolete backed/research `PROJECT`-prefix matches: 0/0.
- Indented subordinate numeric rows: 2.
- Delimiters: variable runs of ASCII spaces; no tabs observed.
- Normalized nonterminal and PTY content: exactly equal, SHA-256
  `9474d6959d751c170b118c80b03b6cf74abc19cd3c316d66dfc6e17db17646aa`.

## Proven defect and ruling

Attempt 002 failed because the active parser required only the owner-context
`PROJECT`-prefix first fields while the live SCC display emitted
`RPROJECT`-prefix first fields. The repaired parser recognizes either exact
alias and treats simultaneous aliases for one role as a duplicate. Header
wrapping, whitespace, child rows, locale, and terminal status were not causal.

The repaired hierarchy is:

1. Exact native `root`-fileset rows establish quota bytes, usage, file quota,
   and files used.
2. `df -B1` establishes physical filesystem bytes.
3. `findmnt` and no-follow path evidence establish mount identity,
   distinctness, filesystem type, bind status, and symlink status.
4. The human display is a secondary cross-check only: `PASS` when parsed and
   concordant, `UNAVAILABLE_NONBLOCKING` when unavailable/unparseable with
   complete primary evidence, and `FAIL_BLOCKING` when a parsed value
   contradicts primary authority.

Displayed usage is compared by decimal half-up rounding at its observed
precision from zero through six decimal places. Nominal quota and file counts
remain exact numeric comparisons. Primary capacity gates report `PASS`,
`FAIL`, or `NOT_EVALUATED`; an upstream evidence failure cannot be reported as
a false negative capacity result.

The diagnostic made zero cloud requests, object listings, scheduler
submissions, DICOM-body requests, or scientific-processing calls.
