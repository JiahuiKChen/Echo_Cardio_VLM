#!/usr/bin/env python3
"""Closed-state parsing and semantic adjudication for GCS Autoclass metadata."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Mapping


RAW_AUTOCLASS_STATES = (
    "KEY_ABSENT",
    "KEY_PRESENT_NULL",
    "KEY_PRESENT_MAPPING_ENABLED_TRUE",
    "KEY_PRESENT_MAPPING_ENABLED_FALSE",
    "KEY_PRESENT_EMPTY_MAPPING",
    "KEY_PRESENT_MAPPING_ENABLED_MISSING",
    "KEY_PRESENT_MALFORMED",
    "REQUEST_OR_RECEIPT_UNPROVEN",
)

EFFECTIVE_AUTOCLASS_STATES = (
    "EXPLICIT_ENABLED",
    "EXPLICIT_DISABLED",
    "ABSENT_CONFIGURATION_DEFAULT_DISABLED",
    "PRESENT_NULL_UNRESOLVED",
    "EMPTY_OR_INCOMPLETE_MAPPING_UNRESOLVED",
    "MALFORMED_OR_UNPROVEN",
)

AUTHORITATIVELY_DISABLED_STATES = (
    "EXPLICIT_DISABLED",
    "ABSENT_CONFIGURATION_DEFAULT_DISABLED",
)


class AutoclassStateError(ValueError):
    """Fail-closed parsing or evidence error with a safe message."""


@dataclass(frozen=True)
class AutoclassAdjudication:
    raw_autoclass_observation_state: str
    effective_autoclass_semantic_state: str
    autoclass_effectively_enabled: bool
    autoclass_authoritatively_disabled: bool
    semantic_evidence_authority: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strict_json_loads(payload: bytes) -> Any:
    """Parse UTF-8 JSON while rejecting duplicate keys and nonfinite numbers."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AutoclassStateError("RAW_RESPONSE_NOT_UTF8_JSON") from exc

    def reject_constant(_: str) -> None:
        raise AutoclassStateError("RAW_RESPONSE_NONFINITE_JSON_NUMBER")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        normalized: set[str] = set()
        for key, value in pairs:
            normalized_key = str(key).strip().casefold()
            if key in output or normalized_key in normalized:
                raise AutoclassStateError("RAW_RESPONSE_DUPLICATE_JSON_KEY")
            output[key] = value
            normalized.add(normalized_key)
        return output

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise AutoclassStateError("RAW_RESPONSE_INVALID_JSON") from exc


def observe_raw_autoclass(payload: Mapping[str, Any]) -> str:
    """Classify the exact JSON shape without assigning semantics."""

    if "autoclass" not in payload:
        return "KEY_ABSENT"
    value = payload["autoclass"]
    if value is None:
        return "KEY_PRESENT_NULL"
    if not isinstance(value, Mapping):
        return "KEY_PRESENT_MALFORMED"
    if not value:
        return "KEY_PRESENT_EMPTY_MAPPING"
    if "enabled" not in value:
        return "KEY_PRESENT_MAPPING_ENABLED_MISSING"
    enabled = value["enabled"]
    if type(enabled) is not bool:
        return "KEY_PRESENT_MALFORMED"
    return (
        "KEY_PRESENT_MAPPING_ENABLED_TRUE"
        if enabled
        else "KEY_PRESENT_MAPPING_ENABLED_FALSE"
    )


def adjudicate_autoclass(
    payload: Mapping[str, Any],
    *,
    bucket_get_succeeded: bool,
    fields_selector_proven: bool,
    content_type_json: bool,
    redirect_occurred: bool,
    raw_response_receipt_verified: bool,
    official_default_disabled_semantics_verified: bool,
) -> AutoclassAdjudication:
    """Map raw observation to an effective state only after all evidence gates."""

    evidence_proven = all(
        (
            bucket_get_succeeded,
            fields_selector_proven,
            content_type_json,
            not redirect_occurred,
            raw_response_receipt_verified,
        )
    )
    if not evidence_proven:
        raw_state = "REQUEST_OR_RECEIPT_UNPROVEN"
        effective_state = "MALFORMED_OR_UNPROVEN"
        authority = "UNPROVEN_REQUEST_OR_RECEIPT"
    else:
        raw_state = observe_raw_autoclass(payload)
        if raw_state == "KEY_PRESENT_MAPPING_ENABLED_TRUE":
            effective_state = "EXPLICIT_ENABLED"
            authority = "VERIFIED_RAW_RESPONSE_EXPLICIT_BOOLEAN"
        elif raw_state == "KEY_PRESENT_MAPPING_ENABLED_FALSE":
            effective_state = "EXPLICIT_DISABLED"
            authority = "VERIFIED_RAW_RESPONSE_EXPLICIT_BOOLEAN"
        elif raw_state == "KEY_ABSENT":
            if official_default_disabled_semantics_verified:
                effective_state = "ABSENT_CONFIGURATION_DEFAULT_DISABLED"
                authority = "VERIFIED_RAW_RESPONSE_PLUS_PRIMARY_GOOGLE_DOCUMENTATION"
            else:
                effective_state = "MALFORMED_OR_UNPROVEN"
                authority = "PRIMARY_SEMANTIC_AUTHORITY_UNPROVEN"
        elif raw_state == "KEY_PRESENT_NULL":
            effective_state = "PRESENT_NULL_UNRESOLVED"
            authority = "VERIFIED_RAW_RESPONSE_NULL_NOT_SEMANTICALLY_ADJUDICATED"
        elif raw_state in {
            "KEY_PRESENT_EMPTY_MAPPING",
            "KEY_PRESENT_MAPPING_ENABLED_MISSING",
        }:
            effective_state = "EMPTY_OR_INCOMPLETE_MAPPING_UNRESOLVED"
            authority = "VERIFIED_RAW_RESPONSE_INCOMPLETE_MAPPING"
        else:
            effective_state = "MALFORMED_OR_UNPROVEN"
            authority = "VERIFIED_RAW_RESPONSE_MALFORMED_AUTOCLASS_VALUE"

    if raw_state not in RAW_AUTOCLASS_STATES:
        raise AutoclassStateError("RAW_AUTOCLASS_STATE_OUTSIDE_CLOSED_ENUM")
    if effective_state not in EFFECTIVE_AUTOCLASS_STATES:
        raise AutoclassStateError("EFFECTIVE_AUTOCLASS_STATE_OUTSIDE_CLOSED_ENUM")
    return AutoclassAdjudication(
        raw_autoclass_observation_state=raw_state,
        effective_autoclass_semantic_state=effective_state,
        autoclass_effectively_enabled=effective_state == "EXPLICIT_ENABLED",
        autoclass_authoritatively_disabled=(
            effective_state in AUTHORITATIVELY_DISABLED_STATES
        ),
        semantic_evidence_authority=authority,
    )
