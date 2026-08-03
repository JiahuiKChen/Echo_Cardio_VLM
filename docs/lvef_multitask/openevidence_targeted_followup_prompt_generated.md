# Targeted OpenEvidence follow-up prompt

Status: `NOT_GENERATED_PENDING_SCC_METADATA`.

No copy-ready OpenEvidence prompt is present in this committed file. The Phase 1C SCC clinical-metadata command failed before producing a packet, so there is no defensible project-specific description/unit authority from which to select a literature-answerable ambiguity.

The next SCC run must use the supported interpreter and generate the restricted metadata packet plus the exact six-file aggregate safety packet. The optional follow-up generator may then do one of three things:

- emit a separately safety-gated copy-ready prompt containing only issues classified `LITERATURE_ANSWERABLE`;
- record `NOT_GENERATED_ZERO_LITERATURE_ANSWERABLE_AMBIGUITIES`; or
- fail closed as `NOT_GENERATED_SAFETY_GATE_FAILED` without reproducing unsafe metadata.

Dataset-identity, alias, unit, pipeline, value-distribution, and clinician-adjudication questions must not be redirected to OpenEvidence. This file must not be replaced with a copy-ready prompt until the generated follow-up safety gate passes and the exact prompt text has undergone aggregate-safe review.
