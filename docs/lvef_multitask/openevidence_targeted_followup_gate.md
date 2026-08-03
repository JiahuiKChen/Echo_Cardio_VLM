# Targeted OpenEvidence follow-up gate

Decision: **do not issue another OpenEvidence prompt yet**.

Clinically consequential ambiguity remains, but the current blockers are project-specific raw definitions and units rather than missing literature volume. OpenEvidence cannot determine whether `tr_mmhg` is a gradient or RVSP, whether `mitral_e_velocity` duplicates `mv_peak_e`, which wall/LA/aortic/IVC conventions were exported, or whether `lvef` mixes methods without seeing the exact non-PHI source metadata.

The SCC-only metadata review and CMR-01 through CMR-15 adjudication must run first. A follow-up is warranted only when all of the following hold:

1. the exact raw name, description, native/normalized unit, and mapping source for the unresolved field can be included without PHI or operational paths;
2. a clinician has identified a literature-answerable ambiguity rather than a database-mapping ambiguity;
3. the question could change feature masking, target disposition, a threshold, or a native-unit margin;
4. the requested evidence can be checked against a professional guideline or measurement-methodology source;
5. the output is required to use exact repository identifiers and cannot introduce candidate field names as if they exist.

If that gate passes, prepare one short prompt per ambiguity with this structure:

```text
We need a targeted clinical measurement-methodology adjudication for one MIMIC-IV-ECHO structured field. Do not infer any additional project field exists.

Exact repository canonical target: <EXACT_ALLOWLISTED_IDENTIFIER>
Exact non-PHI raw name: <FROM_RESTRICTED_REVIEW>
Exact non-PHI raw description: <FROM_RESTRICTED_REVIEW>
Native unit: <FROM_RESTRICTED_REVIEW>
Normalized unit: <FROM_RESTRICTED_REVIEW>
Mapping source: <FROM_RESTRICTED_REVIEW>
Remaining clinician question: <ONE PRECISE QUESTION>

Use this evidence hierarchy: current professional guideline/consensus; peer-reviewed measurement methodology; peer-reviewed clinical observational evidence; formula inference; expert inference; unresolved. Distinguish what the supplied metadata establishes from what the literature supports. Do not repair or rename the exact canonical identifier.

Return one CSV row with exactly these columns:
target,raw_metadata_interpretation,adjudication,evidence_tier,citation,doi,pmid,direct_link,certainty,assumptions,remaining_unresolved,feature_mask_action,target_disposition_action

Use only Yes/No/Unresolved actions. If the source description is insufficient, return UNRESOLVED rather than guessing.
```

Placeholders are intentionally not filled from canonical names or OpenEvidence guesses. Consequently this file is a gated construction template, not a copy-ready clinical query and should not be pasted into OpenEvidence before restricted metadata review.
