# R7H authentication successor

The execution submitted at `91277df0157ff806694a1d84317d25f3df8edb8f`
terminated at the first download attempt with `FAILED_NONRETRYABLE` /
`AUTHENTICATION`. Its four-delta journal, failure receipt, submission and worker
receipts, scheduler evidence, and failed-finalizer authority remain immutable.
Tasks 18 and 19 and the finalizer stopped because predecessor receipts were
absent. No tail batch finalized and no DICOM payload or extraction output was
retained. The exact first 16 final receipts and historical Batch-16 partial are
unchanged.

The bounded successor consumes that evidence under the same scientific attempt,
plan, 530 remaining studies and Tasks 17–19. It does not make authentication
failures retryable or reset the old journal. A changed plan, source membership,
terminal journal, execution identity, or retained payload fails the gate.

## Owner credential renewal

The private login helper resolves the pinned Cloud SDK 579.0.0 executable,
private config and approved account/project from the existing session parsers.
Only the owner runs `auth application-default login --no-launch-browser` with
`CLOUDSDK_CORE_DISABLE_PROMPTS=0`. The owner opens Google's URL in trusted
Chrome and enters the returned authorization code directly into the same SCC
terminal. Tokens, authorization codes, ADC JSON and Google interaction are not
recorded in Codex, the repository or scheduler logs.

After login, the helper checks identity and applies only the established quota
project. The ADC remains owner-private. Credential renewal does not rewrite
scientific runtime receipts, the SDK authority or configuration location.

`lvef_c3_r7h_adc.py` obtains its token through the actual production
`GcloudADCTokenProvider`, including the worker's sanitized noninteractive
subprocess environment. It checks the token's verified identity, the ADC quota
project, and exact generation, byte count, MD5 and CRC32C of one planned source
object through a bounded metadata request. It never uses unrelated CLI login
credentials as proof and never requests object media.

A successful check reports one token command, one identity request and one
source metadata request. Cloud SDK's internal OAuth/cache network count is not
observable and is reported as unknown. Credential values and object paths are
discarded; only closed outcomes and authority digests enter readiness receipts.
The targeted CPU probe performs the same check. Each array worker checks again
immediately before DOWNLOAD, so a prior login or queued-probe PASS cannot
replace current credential readiness.

## Execution and publication

`lvef_c3_r8u_r7h_auth_successor.py` owns a distinct, fixed successor control
namespace. Fresh capacity, credential probe, claim, scheduler submissions,
worker identities and finalizer bindings consume the exact original failure.
One array contains original Tasks 17–19 with maximum concurrency one; its CPU
finalizer holds on that exact new array identity.

Canonical payload and scientific output paths remain unchanged. Each batch's
new download journal, receipts and execution binding reside in its explicit
`r7h_auth_successor_v1` control directory. The original journal and receipts
remain readable at their original paths. The existing recursive preservation
inventory retains both histories. A new authentication failure terminates the
new execution under the same nonretryable rule.

The consumed finalizer authority is retained. A separate successor binding
authorizes the new finalizer and its implementation epoch. Canonical outputs
and the cohort aggregate retain no-clobber publication. Scientific closure
still requires all 19 valid batch receipts and
`PASS_PRODUCTION_C3_FINALIZED`, with the existing embedding/preservation replay,
fixed cohort counts and confirmatory-analysis restrictions.

Capacity is a new resource observation with its own producer and raw captures;
the original capacity receipt is not relabelled or treated as current headroom.
Queue absence is not terminal success. Ambiguous submission results must be
reconciled from sealed scheduler evidence before another submission is allowed.

Focused regressions cover the terminal journal, new control namespace, altered
bindings, retained payload, renewed authentication failure, credential identity
and metadata access, freshness, and publication collisions. The maintained
suite is run once after substantive shared changes, followed by the applicable
syntax and export checks. No fitting, endpoint prediction, new technical
dispositions or confirmatory-performance access is authorized.
