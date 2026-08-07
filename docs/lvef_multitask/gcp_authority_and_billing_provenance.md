# Google Cloud authority and billing provenance

Status: **prospective authority nominated, runtime authentication and billing verification pending**. This record does not authorize the full selected-cohort DICOM transfer, extraction, embedding, modeling, or confirmatory-performance access.

Recorded: 2026-08-07.

## Three noninterchangeable authority classes

### `HISTORICAL_AUTHORITY`

This class covers every Google Cloud Storage download, BigQuery job, source manifest, frozen result, and completed run created before the prospective pivot. Its account and project association must remain exactly as contemporaneously captured. If an old artifact does not establish that association, its value is `UNKNOWN_NOT_RETROACTIVELY_ASSIGNED`.

The prospective project may not be backfilled into historical receipts, manifests, frozen results, logs, or narrative provenance. Historical scientific authority is unchanged.

### `ACTIVE_PROSPECTIVE_AUTHORITY`

This class covers only new metadata requests, future requester-pays downloads, new BigQuery jobs, and prospective C3 reconstruction. The owner supplied a new exact Google identity and project through an SCC-only controlled interaction. Git records the authority class and verification requirements, not those exact identifiers.

Activation requires all of the following to pass against the SCC-only expected values:

1. a supported Cloud SDK or API fallback is resolved without modifying historical environments;
2. the active user identity equals the owner-approved identity;
3. the configured/requester-pays project equals the owner-approved project;
4. the project exists and its Cloud Billing linkage is active;
5. an authenticated metadata-only request to the release authority succeeds with that requester-pays project;
6. the intended BigQuery job/billing project is verified without reading restricted result rows;
7. the resulting receipt reports only Boolean/status fields, tool provenance, and checksums.

An owner-entered requester-pays value and a `PREFLIGHT_ENV_READY` receipt establish controlled input capture only. They do not establish authentication, project access, billing linkage, dataset access, or budget approval.

### `CREDENTIAL_STATE`

Authentication material remains SCC-only. OAuth tokens, Application Default Credentials, service-account keys, Cloud SDK state, requester-pays session files, billing-account identifiers, payment details, and exact account/project values may not enter Git, an ordinary response, or a manuscript export. Controlled files must use restrictive permissions and remain outside the repository. Terminal and scheduler logs must not echo secrets.

Aggregate-safe authentication receipts may report fields such as:

- `active_identity_matches_expected`;
- `configured_project_matches_expected`;
- `billing_link_active`;
- `requester_pays_metadata_access_passed`;
- `bigquery_billing_project_access_passed`;
- `free_trial_status`;
- tool source, executable checksum, and nonsecret version;
- zero token or credential bytes emitted.

They may not contain the compared identity, project ID, billing-account ID, token, credential path, or payment data.

## Billing and Free Trial ruling

Project existence is not evidence of active billing. An active Cloud Billing link must be verified separately and recorded as an aggregate Boolean. Likewise, successful requester-pays access does not prove that a Free Trial credit is available.

The Cloud Billing project API can establish whether billing is enabled, but it does not reliably expose the end-user Free Trial credit balance, expiration, or remaining eligibility. Therefore the current trial disposition is `NOT_API_VERIFIABLE_REQUIRES_OWNER_CONSOLE_OR_BILLING_RECORD`. This is not converted to `PASS` merely because the project exists or billing is enabled. Before body transfer, budget approval must remain independently documented; trial credit is a contingency source, not a scientific authority.

## Portability boundary

The metadata preflight does not depend on `gcloud` being globally installed. The SCC resolver requires Cloud SDK 579.0.0 and canonicalizes the selected executable before recording it. If no supported installation exists, a separate prepare-only helper creates an owner-reviewable bootstrap for the exact official versioned archive; it pins compressed and decompressed payload checksums, installs only below the research-tier tools root, and changes no profile or system package. The resulting isolated Cloud SDK configuration remains below the preserved restricted run root. Authentication is owner-interactive; every later probe revalidates identity, project, billing, requester-pays metadata access, BigQuery dry-run access, tool/command checksums, and a restricted immutable receipt without printing tokens or identifiers.

The API client rejects redirects, non-JSON responses, oversized control-plane responses, ambient credential overrides, and service-account impersonation. It permits metadata/control-plane endpoints only. Failure to resolve or authenticate this path blocks prospective cloud access only; it does not invalidate completed storage audits or historical artifacts.

## Current execution decision

- Completed SCC storage audits and their run root remain preserved.
- Prospective metadata outputs may be regenerated only when their billing/authentication provenance changes or an earlier attempt failed before producing an authoritative output.
- Object bodies remain prohibited.
- Full C3 remains **NO-GO** until all preauthorization gates, including prospective authentication/billing, exact source metadata, resource headroom, budget approval, frozen authorities, implementation validation, and explicit owner authorization, pass.
