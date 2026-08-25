# apple-mail-mcpb/ — Claude Desktop bundle

Build files for the **`.mcpb`** distributable. Same Python server as [`plugin/`](../plugin/) — copied at build, not a separate codebase.

> **One of three artifacts.** See root [`CLAUDE.md`](../CLAUDE.md) § Distribution channels for the full map:
> `.mcpb` here ships the Claude Desktop chat extension; `apple-mail-plugin.zip` is the Claude Code marketplace zip; `apple-mail.plugin` is the byte-identical Cowork upload artifact. All three rebuild from `tools/gates/build-artifacts.sh` in one shot.

| File | Role |
|------|------|
| `manifest.json` | `manifest_version` (MCPB spec 0.3), version, `tools[]`, `prompts[]`, `user_config`, server entry, `compatibility`, `privacy_policies`, `documentation`, `support` |
| `build-mcpb.sh` | Stage `plugin/` → zip `../apple-mail-mcp-v{VERSION}.mcpb` |

```bash
bash tools/gates/dev-check.sh release
```

Use `cd apple-mail-mcpb && ./build-mcpb.sh` only for bundle-only debugging; the release gate rebuilds all three distributables and runs the validator/test/smoke stack.

Copies `apple_mail_mcp.py`, `start_mcp.sh`, `requirements.txt`, `requirements.lock`, `wheelhouse/`, `apple_mail_mcp/`, mirrored `plugin/skills` → **`skills/`**, and `ui/` in build output, plus this folder's `manifest.json` and `README.md`. No venv is bundled: `start_mcp.sh` creates one from the offline macOS arm64 CPython 3.13 payload. Keep the embedded README platform and Python 3.13 requirements in sync.

**Build must use `mcpb pack`** when available (official CLI, `npm install -g @anthropic-ai/mcpb`). Raw `zip -r .` emits zero-byte directory entries that `mcpb unpack` and Claude Desktop's installer treat as files — install fails with `ENOENT: no such file or directory, open '.../ui/'`. `build-mcpb.sh` prefers `mcpb pack` and falls back to `zip -X -D` only when the CLI is missing. `tools/validators/validate_manifests.py` (checks in `tools/manifest_checks/`) enforces exact artifact membership plus the no-directory-entry rule on every commit.

## tools[] must match code

Full `tools[]` in `manifest.json` must list every `@mcp.tool` name in code; description must claim correct count (**41**). Count with `find plugin/apple_mail_mcp/tools -name '*.py' | xargs grep -h '^@mcp.tool' | wc -l` (recursive; package-nested tools count). Validated by [`tools/gates/validate_manifests.sh`](../tools/gates/validate_manifests.sh).

## Directory submission fields

Claude Desktop extension-directory submissions require a current manifest, so `manifest.json` carries:

| Field | Value | Why |
|-------|-------|-----|
| `manifest_version` | `"0.3"` (first key) | Current MCPB spec; the legacy `dxt_version` key is deprecated and the validator rejects it |
| `compatibility.platforms` | `["darwin"]` | Mail.app and Calendar.app exist only on macOS; the spec has no arch field, so the arm64-only wheelhouse is documented in the README instead |
| `compatibility.runtimes.python` | `">=3.13,<3.14"` | `start_mcp.sh` hard-requires `python3.13` and the offline wheelhouse ships `cp313` arm64 wheels; `pyproject.toml`'s `>=3.10` describes the PyPI package, not this bundle |
| `privacy_policies` | HTTPS link to `PRIVACY.md` on `main` | Missing or non-HTTPS privacy policies are an immediate directory rejection |
| `support` | GitHub issues URL | Optional in the spec; the directory listing asks for a support contact |

`server.type` stays `"python"` (`uv` needs spec 0.4 and an unbundled `pyproject.toml` install, which this offline payload does not use). `_check_mcpb_directory_contract` in `tools/manifest_checks/install_contracts.py` enforces every row above on each commit. Cross-check the schema with the official CLI without installing it globally:

```bash
npx -y @anthropic-ai/mcpb@latest validate apple-mail-mcpb/manifest.json
```

Spec: https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md · Submission rules: https://claude.com/docs/connectors/building/submission

## vs plugin/ and Cowork

| | Claude Code | Claude Desktop (chat) | Claude Desktop (Cowork) |
|---|-------------|------------------------|--------------------------|
| Manifest | `plugin/.claude-plugin/plugin.json` | `manifest.json` (MCPB spec 0.3) | `plugin/.claude-plugin/plugin.json` |
| Discovery | `.claude-plugin/marketplace.json` | Direct `.mcpb` install via "Add Custom Plugin" / "Install from file" | Customize → Add plugin → Upload plugin (accepts `.plugin`) |
| Artifact | `apple-mail-plugin.zip` | `apple-mail-mcp-v{VERSION}.mcpb` | `apple-mail.plugin` (byte-identical to the `.zip`) |
| Entrypoint | `start_mcp.sh` via `mcpServers` in `plugin.json` | `start_mcp.sh` via `manifest.json` `server.mcp_config` | `start_mcp.sh` via `mcpServers` in `plugin.json` |

Version sync: use the release version table in root [`CLAUDE.md`](../CLAUDE.md) § Version bump (seven files, including `plugin/.cursor-plugin/plugin.json`); the authoritative check is `_public_version_checks()` in [`tools/validators/validate_manifests.py`](../tools/validators/validate_manifests.py). Deferred release/backlog items live in [`tasks/reference/robustness-backlog-2026-05-22.md`](../tasks/reference/robustness-backlog-2026-05-22.md).

## Related

[`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) · [`tools/CLAUDE.md`](../tools/CLAUDE.md)
