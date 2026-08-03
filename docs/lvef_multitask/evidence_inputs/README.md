# External clinical evidence inputs

These files preserve the complete OpenEvidence response text as received, solely as evidence inputs. They are not registry, mapping, panel, threshold, margin, or citation authority. The responses contain star-mangled identifiers, citation-marker insertions, malformed/truncated CSV rows, and incomplete prose; downstream drafts use an exact repository identifier allowlist and manual claim verification rather than importing these files.

`apply_patch` added one terminal POSIX line-feed byte to each repository text file because the source attachments lacked a final newline. A byte comparison confirms that each original attachment is an exact prefix of its repository copy and that no other byte differs. Therefore these are complete content-preserving copies with a documented terminal-newline normalization, not byte-for-byte copies.

| Evidence input | Original bytes | Original SHA-256 | Repository bytes | Repository SHA-256 | Transformation |
|---|---:|---|---:|---|---|
| `openevidence_dependency_response_verbatim.txt` | 23,789 | `ee2bd68a7bfb86c098f6d4b65190c916a92d87108a0aad0e0f183e778b544ea7` | 23,790 | `bfbd328b2c88edbd259acf6fdbe79df482307f90dee0e99d8af200871feb21e2` | one terminal LF added |
| `openevidence_threshold_margin_response_verbatim.txt` | 38,349 | `6658e531ccbc2f8feb9682da44c4a7e32376d585936f001833f77835f32ae28d` | 38,350 | `d747d4158a3a3d6f3ba49c059d7c621674e06472b5c7df87cc683c7936f7d8b3` | one terminal LF added |

The attachment paths are intentionally omitted because they are workstation-local provenance, not portable scientific authority.
