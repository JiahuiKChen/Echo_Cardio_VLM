# Final audit lock workflow

Operational scope only: validate the completed clarified V3 records, create an
immutable copy, record the owner's repeat-review feasibility amendment, and
prepare a blinded adjudication queue. This tooling does not fit models, read
target values, interpret images, change annotations, or estimate reliability.

`finalize_jdim_audit.py` must run under the authenticated project Unix account
on SCC. It checks root ownership/group membership and restricted permissions.
Never read or transmit the browser owner secret. Every mutating command requires
the typed phrase `FINALIZE JDIM-D-26-02840 WITHOUT CHANGING REVIEWS`.

Before `finalize`, verify that the review server has exited, no review is in
progress, and no review writer is running. The queue lock is held during
backup and validation; hashes must reconcile before and after. The byte-level
backup completes and verifies before saved annotation content is validated. Backups are
exclusive-create tar archives, verified member-by-member and made read-only.
Existing review files and protocol definitions are never rewritten. An
interrupted finalization root must be preserved, not reused or overwritten.

Use the exact deployed review SHA separately from the finalization tooling SHA.
The latter is discovered from clean committed tooling by the CLI. The former
continues to identify the unchanged default-preset configuration.

```sh
$PY scripts/finalize_jdim_audit.py validate \
  --package-root "$AUDIT_ROOT" --expected-user "$PROJECT_USER" \
  --review-source-commit "$REVIEW_SHA"
$PY scripts/finalize_jdim_audit.py finalize \
  --package-root "$AUDIT_ROOT" --expected-user "$PROJECT_USER" \
  --review-source-commit "$REVIEW_SHA" --output-root "$FINAL_ROOT" \
  --review-service-stopped
$PY scripts/finalize_jdim_audit.py verify-lock \
  --package-root "$AUDIT_ROOT" --expected-user "$PROJECT_USER" \
  --review-source-commit "$REVIEW_SHA" --output-root "$FINAL_ROOT"
$PY scripts/finalize_jdim_audit.py queue \
  --package-root "$AUDIT_ROOT" --expected-user "$PROJECT_USER" \
  --review-source-commit "$REVIEW_SHA" --output-root "$FINAL_ROOT"
```

`FINAL_ROOT` must be a new child of `$AUDIT_ROOT/restricted/finalization`.
No case-level data from that directory may be exported. Console output is
operational metadata only. The original eight-study repeat subset is unchanged;
the two completed Secondary records are checked against their paired Primary
records. Same-reviewer pairs are excluded from repeat QC, not from Primary data.

The queue includes measurement-related positives, uncertainties, not-assessable
fields needing resolution, entered candidate values and valid paired disagreements.
Generic text/numeric/unit positives and B-mode/color Doppler alone do not trigger
the queue. Tissue Doppler, mixed and other modality classifications are sent for
human relevance resolution, never interpreted from pixels or free-text notes.
It makes no adjudication choices. Queue case tokens
are new opaque identifiers. Study linkage is stored separately and must not be
served to readers. Do not use this queue to infer final prevalence or access
report labels. `finalize` runs backup, integrity validation, Primary lock,
repeat amendment, blinded annotation lock and queue construction in one invocation.
`queue` is only for a successful separate `freeze`, never after `finalize`.
If validation fails, the new backup and aggregate-only blocker certificate remain;
no Primary lock or queue is issued. Preserve this root unchanged.

For a nonempty queue:

```sh
$PY scripts/finalize_jdim_audit.py technical-check \
  --package-root "$AUDIT_ROOT" --output-root "$FINAL_ROOT" \
  --expected-user "$PROJECT_USER" --review-source-commit "$REVIEW_SHA" \
  --media-root "$PROTECTED_MEDIA_ROOT"
$PY scripts/finalize_jdim_audit.py serve \
  --package-root "$AUDIT_ROOT" --output-root "$FINAL_ROOT" \
  --expected-user "$PROJECT_USER" --review-source-commit "$REVIEW_SHA" \
  --media-root "$PROTECTED_MEDIA_ROOT" --port 8767
$PY scripts/finalize_jdim_audit.py lock-adjudication \
  --package-root "$AUDIT_ROOT" --output-root "$FINAL_ROOT" \
  --expected-user "$PROJECT_USER" --review-source-commit "$REVIEW_SHA"
```

Use the existing authenticated SSH tunnel to the service's loopback port. The
physician enters their registered pseudonymous code, attests qualification, views
the appropriate scored panel, explicitly selects every queued decision, confirms
and locks it. Choices start blank; original reader responses remain unchanged.
Locked adjudication records are exclusive-create and cannot be edited. Progress
persists in `FINAL_ROOT/human_adjudication` across sessions. Only new opaque
adjudication tokens reach the client; source linkage and media filenames remain
server-side. Host, Origin, session-cookie and CSRF checks protect write routes.
No secrets appear in URLs, request logs or console output. Original owner browser
credentials are never accessed. `technical-check` inspects PNG headers, not pixels.

If the queue is empty, `export` is permitted immediately. The bounded export
requires the no-op adjudication certificate before reading target membership,
keeps evidence tiers separate and emits only aggregate categorical counts and
exact binomial study intervals. It never assumes clip independence. Empty-queue
candidate matching is not applicable because any entered value triggers human
adjudication. The exporter fails closed for nonempty queues: post-adjudication
candidate matching is a separate gated step, not an automated clinical choice.

After completed human adjudication, use the distinct bounded export path:

```sh
$PY scripts/finalize_jdim_audit.py preflight-adjudicated-export \
  --package-root "$AUDIT_ROOT" --output-root "$FINAL_ROOT" \
  --expected-user "$PROJECT_USER" --review-source-commit "$REVIEW_SHA"
$PY scripts/finalize_jdim_audit.py export-adjudicated \
  --package-root "$AUDIT_ROOT" --output-root "$FINAL_ROOT" \
  --expected-user "$PROJECT_USER" --review-source-commit "$REVIEW_SHA"
```

Both commands require the explicit confirmation phrase. The preflight writes
nothing, including no administrative event. Every queued human decision must
have an exact completion-lock hash, valid qualification/confirmation, and valid
blinded linkage. Choices are applied only to an in-memory copy of Primary clips;
the existing V3 roll-up supplies the analytical study summaries. Original
Primary/Secondary reviews, their locked summaries, the adjudication records, and
all earlier certificates remain byte-identical. Target assignments are read
only after these gates pass. Report-label values and images are never read.

This path is deliberately limited to completed adjudication with no remaining
candidate-value matching requirement. Any candidate presence, uncertainty,
non-assessability, or entered candidate number fails closed before membership is
read. It does not infer a match or change a human answer. The aggregate-only
summary records the completion-lock hash, number of applied items, and aggregate
number of changed clip fields. Repeated export cannot overwrite an earlier
aggregate directory. No new models or other scientific analyses are run.

Pre-preset clarified reviews are validated as complete attributable manually
entered clip records plus confirmed derived study summary. V3.1 reviews also
require every explicit per-clip confirmation. Displayed defaults never count.
