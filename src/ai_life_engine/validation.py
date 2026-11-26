# SPDX-License-Identifier: Apache-2.0
"""Shared validators for public configuration and persisted records."""

from __future__ import annotations

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "secret",
        "password",
        "passwd",
        "authorization",
        "private_key",
    }
)
_SECRET_SUFFIXES = ("_api_key", "_token", "_secret", "_password", "_private_key")


def clean_name(value: str) -> str:
    """Return a display name or raise ValueError."""

    if not isinstance(value, str):
        raise ValueError("Name must be a string.")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 128:
        raise ValueError("Name must be between 1 and 128 characters.")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError("Name must not contain control characters.")
    return cleaned


def _normalized_metadata_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def metadata_key_is_secret(key: str) -> bool:
    """Return True when a metadata key looks like a credential."""

    normalized = _normalized_metadata_key(key)
    return normalized in _SECRET_KEYS or normalized.endswith(_SECRET_SUFFIXES)


def clean_metadata(value: dict[str, str]) -> dict[str, str]:
    """Return a copy of metadata or raise ValueError."""

    if not isinstance(value, dict):
        raise ValueError("Metadata must be a mapping of strings.")
    if len(value) > 32:
        raise ValueError("Metadata supports at most 32 entries.")
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("Metadata keys and values must be strings.")
        if not key.strip() or len(key) > 64 or len(item) > 256:
            raise ValueError("Metadata keys must be 1..64 characters and values 0..256.")
        if any(ord(character) < 32 for character in key + item):
            raise ValueError("Metadata must not contain control characters.")
        if metadata_key_is_secret(key):
            raise ValueError("Metadata must not contain secrets such as API keys or tokens.")
        cleaned[key] = item
    return cleaned


def clean_reason(value: str, *, limit: int = 256) -> str:
    """Return a single-line reason suitable for logs and the audit trail."""

    if not isinstance(value, str):
        raise ValueError("Reason must be a string.")
    cleaned = "".join(character if character.isprintable() else " " for character in value)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        raise ValueError("Reason must not be empty.")
    return cleaned[:limit]


def bounded_limit(limit: int, *, maximum: int = 1000) -> int:
    """Return a list limit in the inclusive range 1..maximum."""

    if type(limit) is not int or limit < 1 or limit > maximum:
        raise ValueError(f"Limit must be an integer from 1 to {maximum}.")
    return limit
