# Reviewed public source exports

A local operator repository can contain private history, reflogs, branches, tags and runtime material even after its current files have been cleaned.
Never publish it with `push --all`, `--mirror`, a bundle, a local clone, or an unreviewed branch push.
Do not add the public remote to an operational checkout.

`public-export.json` is the explicit file allowlist and exclusion manifest.
Every committed file must appear in exactly one category; new files stop the export until classified.
The export includes the complete current TUI, local web UI, tests, generic setup documentation and MIT attribution.
It excludes screenshots, retired push/icon-generation tools and unused PWA artifacts.
Untracked files, ignored overlays, Git metadata, source refs, commit messages, private reports and runtime state never enter the archive.
The synthetic test fixtures are source fixtures, not a copied operator home.

## Create and verify an artifact

Commit the reviewed source and leave it clean, then run:

```sh
python3 scripts/export_public.py --archive /absolute/staging/deck-source.tar --attestation /absolute/private/deck-export-attestation.json
python3 scripts/export_public.py --archive /absolute/staging/deck-source.tar --verify
```

The archive contains only allowlisted Git blobs plus `PUBLIC-MANIFEST.json` with per-file SHA-256 hashes and modes.
An existing public `PUBLIC-MANIFEST.json` is excluded from source inputs and regenerated, so public checkouts can export updates without hashing an older manifest into the next one.
Fixed tar metadata excludes source usernames and timestamps.
The separate attestation records the exact source commit/tree, private source path and archive/manifest hashes.
**Keep that attestation private.**
It is not part of the archive and must never be committed to the public repository.
The archive has no source branch or private commit ID.
Re-exporting the same commit produces identical archive bytes.
The scanner rejects concrete private tailnet hostnames and common credential markers; it is a backstop, not proof that arbitrary added prose is safe.
Review allowlist changes and their actual content before each export.

## Initial public history

Use a separate publisher checkout cloned from the actual public upstream URL in the manifest, not from a local operational repository.
Verify the manifest's known-public base commit is present on that upstream's public history.
Start from that base and apply only the verified archive's file tree as one new public commit, with a neutral public commit message and deliberately chosen public author identity.
Remove the explicit `exclude` paths from that publication tree.
Do not carry local branch names, commit messages, Git configuration or private attestations across.
This preserves the upstream MIT attribution and known-public history while omitting all private customization history.
It also leaves a normal upstream merge/update relationship for future maintenance.
Review the resulting tree and all reachable refs before any separate publication step.
No tool in this repository creates a remote, pushes, opens an issue, or publishes a release.

## Later updates

Keep private operational overlays outside both source and publication checkouts.
Make reusable code changes in the source, update the allowlist when necessary, run the focused tests, commit and export again.
In the publication checkout, compare the new `PUBLIC-MANIFEST.json` with the previous public manifest.
Apply changed/new files and remove only previously exported files no longer in the new manifest, plus explicitly excluded paths.
Do not use an indiscriminate filesystem mirror over `.git` or private configuration.
Review that patch on the public history and make a new normal commit; never replace public history with operator history.
Bring public fixes back as reviewed source patches, leaving private runtime choices in the external config.

The CI files in the allowlist only run tests with read-only repository permissions.
The producer contract check is manual and uses recorded fixtures; it cannot create issues or trigger an upstream write.
Screenshots and generated proof remain private unless separately recreated using synthetic data and explicitly added after review.
