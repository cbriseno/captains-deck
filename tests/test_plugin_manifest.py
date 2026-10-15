#!/usr/bin/env python3
"""herdr-plugin.toml must stay acceptable to the Herdr plugin marketplace.

The marketplace indexer reads every `herdr-plugin.toml` on the default branch and
drops a manifest it cannot accept. It reports the drop only as a count, so a
repository disappears from https://herdr.dev/plugins/ with no error anywhere.
That is how deimantasnork/captains-deck left the index on 2026-10-11: the
description had grown to 607 characters.

The rules below mirror parseManifestSummary in the marketplace worker
(workers/plugin-marketplace/src/index.ts in herdrdev/herdr).

Also runs standalone: python3 tests/test_plugin_manifest.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "herdr-plugin.toml"

# Marketplace limits. The description cap is the one this repository broke.
DESCRIPTION_MAX_CHARS = 500
ID_MAX_CHARS = 120
NAME_MAX_CHARS = 120
VERSION_MAX_CHARS = 64
PLATFORMS = {"linux", "macos", "windows"}

ID_RE = re.compile(r"^[A-Za-z0-9:._-]+$")
DIGITS_RE = re.compile(r"^\d+$")


def load_manifest() -> dict:
    """Read the manifest, also on a Python older than 3.11."""
    text = MANIFEST.read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # the stdlib TOML reader arrived in Python 3.11
        return top_level_values(text)
    return tomllib.loads(text)


def top_level_values(text: str) -> dict:
    """Fallback reader for the keys before the first table header."""
    values: dict = {}
    for line in text.splitlines():
        if line.startswith("["):
            break
        key, sep, raw = line.partition("=")
        if not sep:
            continue
        raw = raw.strip()
        if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            values[key.strip()] = raw[1:-1]
        elif raw.startswith("[") and raw.endswith("]"):
            values[key.strip()] = re.findall(r'"([^"]*)"', raw)
    return values


def is_version_triple(value: str) -> bool:
    """The marketplace accepts X.Y.Z, with an optional leading 'v'."""
    normalized = value[1:] if value.startswith("v") else value
    parts = normalized.split(".")
    return len(parts) == 3 and all(DIGITS_RE.match(part) for part in parts)


def test_description_fits_the_marketplace_cap() -> None:
    description = load_manifest().get("description")
    assert description is None or len(description) <= DESCRIPTION_MAX_CHARS, (
        f"description is {len(description)} characters, and the marketplace drops a "
        f"manifest above {DESCRIPTION_MAX_CHARS}. Keep the extra detail in README.md."
    )


def test_required_metadata_is_valid() -> None:
    manifest = load_manifest()
    problems = []

    ident = manifest.get("id") or ""
    if not ident or len(ident) > ID_MAX_CHARS or not ID_RE.match(ident):
        problems.append(
            f"id {ident!r} must be 1-{ID_MAX_CHARS} characters of A-Za-z0-9:._-"
        )

    name = manifest.get("name") or ""
    if not name or len(name) > NAME_MAX_CHARS:
        problems.append(f"name must be 1-{NAME_MAX_CHARS} characters: {name!r}")

    version = manifest.get("version") or ""
    if not version or len(version) > VERSION_MAX_CHARS:
        problems.append(
            f"version must be 1-{VERSION_MAX_CHARS} characters: {version!r}"
        )

    min_version = manifest.get("min_herdr_version") or ""
    if not min_version or len(min_version) > VERSION_MAX_CHARS:
        problems.append(
            f"min_herdr_version must be 1-{VERSION_MAX_CHARS} characters: {min_version!r}"
        )
    elif not is_version_triple(min_version):
        problems.append(f"min_herdr_version must be X.Y.Z: {min_version!r}")

    platforms = manifest.get("platforms")
    if platforms is not None and (
        not isinstance(platforms, list)
        or not platforms
        or len(set(platforms)) != len(platforms)
        or not set(platforms) <= PLATFORMS
    ):
        problems.append(
            f"platforms must be a non-empty list without repeats, each of "
            f"{sorted(PLATFORMS)}: {platforms!r}"
        )

    assert not problems, "\n".join(problems)


if __name__ == "__main__":
    test_description_fits_the_marketplace_cap()
    test_required_metadata_is_valid()
    print("ok")
