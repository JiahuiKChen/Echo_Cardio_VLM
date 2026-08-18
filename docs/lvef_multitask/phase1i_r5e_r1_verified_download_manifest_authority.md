# Phase 1I-R5E-R1 verified-download manifest authority

The stopped R5E cleanup was adjudicated without reading DICOM or NPZ bodies.
All four fixed Batch-1/Batch-2 verified-download manifests were owner-private,
mode 0600, six-column UTF-8 artifacts with exact batch row sets, successful
download tokens, plan ownership, and observed local SHA-256 authority.  Their
`source_relative_path` field records the immutable plan's original source
authority locator; the physical retained object remains independently bound by
`physical_source_key` and the fixed `<physical_source_key>.dcm` leaf.

R5E-R1 therefore adds one closed role registry for the older and R4 Batch-1/2
artifacts.  Each role fixes its attempt, batch, producer era, ordered header,
literal Boolean token, planned-source-locator convention, row count, byte
length, and SHA-256 before projecting to the common comparison record.  There
is no schema auto-detection, case folding, whitespace normalization, manifest
rewrite, caller-supplied schema/path, or body rehash.  The deletion scope
remains exactly the two older raw-object leaves; R4 and every retained evidence
class remain outside the deletion boundary.
