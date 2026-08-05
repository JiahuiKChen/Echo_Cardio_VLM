# Institutional restricted-AI authorization

Status: **`INSTITUTIONALLY_AUTHORIZED`**
Date recorded in the project: **2026-08-05**

## Authority recorded

The project owner attests that the institution has approved use of an institutionally managed ChatGPT Enterprise, Edu, Healthcare, or API organization with the contractual, retention, access-control, and data-handling protections required for this project. The exact tenant/product label remains part of the institution's authorization record; this repository records the approved organizational class and the owner's attestation, not a broader vendor claim.

This authority permits an approved Codex/API agent operating on SCC to inspect restricted data when scientifically or technically necessary. Permitted inspection includes exact schemas and values; subject and study identifiers; row-level clinical and imaging-linked records; DICOM locators, metadata, and pixel content; extracted arrays; embeddings; predictions; discrepancy rows; and restricted stdout, stderr, and tracebacks. Those details must remain in the approved SCC environment.

The previous absence of an approved restricted-agent channel is therefore **not a project blocker**. Schema obfuscation is not required as a condition of approved SCC analysis. Schema-only inspection remains available as a useful diagnostic when full rows are unnecessary.

## Boundaries that remain in force

This authorization does not:

- permit restricted content to be committed to Git or copied to an unapproved workspace;
- permit unrestricted export of identifiers, patient/study rows, DICOM locators or pixels, extracted arrays, embeddings, predictions, discrepancy rows, or detailed logs;
- permit unnecessary reproduction of identifiers or patient rows in ordinary agent responses;
- supersede the DUA, SCC access controls, institutional policy, or human scientific-review requirements;
- authorize confirmatory performance access, predictive model fitting, the full C3 DICOM transfer, full extraction, or full embedding generation;
- modify the accepted ASA abstract or frozen historical-results authority.

Detailed direct-agent receipts, paths, row-level findings, and approval manifests remain restricted. Git and manuscript exports require a separate, schema-specific safety review, byte-level hash binding, explicit human approval, release receipt, and staged-Git gate under [the secure analysis bridge](secure_analysis_bridge.md) and [`lvef_multitask_safe_export_policy.yaml`](../../configs/lvef_multitask_safe_export_policy.yaml).

## Responsibility and revocation

The project owner remains responsible for ensuring that the active SCC agent session belongs to the approved institutional organization and that institutional approval remains current. If that authority is withdrawn, expires, or cannot be established for a future session, direct restricted-agent execution must stop. A direct-analysis receipt documents the authority used for a run but does not itself create or extend institutional authorization.
