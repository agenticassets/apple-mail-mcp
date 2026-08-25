---
name: marketplace-release-handoff
description: Prepare or resume an Apple Mail Marketplace update from a signed source tag. Use when a source release needs promotion, Marketplace admission, plugin refresh, or concise release handoff guidance.
---

# Marketplace release handoff

Use this skill only after the source change is merged and the source release
gate has passed. It keeps the source and Marketplace responsibilities separate
without rediscovering the release process.

## Fast path

1. From clean source `main`, run:

   ```bash
   bash tools/gates/source-release-gate.sh
   bash tools/gates/create-release-tag.sh --confirm-create
   git push origin vX.Y.Z
   bash tools/gates/marketplace-handoff.sh vX.Y.Z
   ```

2. Treat the handoff output as the only source of the tag, commit, signed source
   inventory hash, target, and selector. Do not transcribe these values manually.
   The Marketplace tool calculates the separate candidate payload digest.

3. In a clean `chore/*` branch of the central Marketplace, run the printed
   command — currently `python3 tools/prepare_plugin_update.py --plugin apple-mail
   --prepare --next-steps`, run from the Marketplace checkout root (that script
   lives in the Marketplace repository, not in this one). Complete
   its candidate-specific evidence, admission, attestation, release gate, and
   reviewed PR steps there. Commit and push the candidate plus redacted proof
   files before admission, because its evidence URLs must be reachable from
   `origin`.

4. After the Marketplace PR merges, return to this repository and run:

   ```bash
   bash tools/gates/refresh-central-marketplace.sh
   ```

   Restart Claude Code and Codex so their MCP schemas reload.

## Boundaries

- The handoff command is read-only and verifies the signed remote source tag.
- It does not create a tag, publish a package, update a Marketplace checkout,
  merge a PR, or claim untested client support.
- The Marketplace owns evidence, admission, and attestation. Do not edit its
  promoted Apple Mail payload directly.
- Cursor direct runtime proof and Cursor Team Marketplace UI admission are
  distinct facts. Do not substitute one for the other.

For the concise human reference, read `docs/marketplace-release-handoff.md`.
