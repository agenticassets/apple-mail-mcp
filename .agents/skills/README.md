# Repo agent skills

Development and maintenance skills for working **on** this codebase. Not shipped
to users — the 11 user-facing workflow skills live in
[`plugin/skills/`](../../plugin/skills/) and go out in the plugin bundle.

`.agents/skills/<name>/SKILL.md` is the single canonical copy. Each skill is
exposed to Claude Code through a relative symlink at
`.claude/skills/<name>` → `../../.agents/skills/<name>`; never keep a duplicate
copy per tool. Adding a skill means all three steps: the directory here, the
symlink, and a commit.

## Locally authored

Written for this repo, no upstream. Edit freely.

| Skill | Purpose |
|-------|---------|
| `apple-mail-archive-export` | Bulk export/archive workflows against Mail.app |
| `context-guidance-audit` | Audit the repo's own `CLAUDE.md` / `AGENTS.md` guidance for drift |
| `finalize-apple-mail-mcp` | Ship sequence: validators, gates, doc sync, artifact rebuild, commit |
| `mail-scripting-dictionary` | Mail.app AppleScript dictionary reference for plugin maintainers |
| `marketplace-release-handoff` | Tag, release, and central-marketplace promotion handoff |

## Vendored, and locally corrected

Copied in from elsewhere. Six are pinned in [`skills-lock.json`](../../skills-lock.json);
four carry **no provenance record at all** — no lock entry and no frontmatter
naming an upstream — so they cannot be re-synced or audited against a source.
**All but one have been edited locally** because their generic guidance was
wrong for this repo — they named build tools this project does not use (`uv`,
`prek`, `pytest-asyncio`, `line_profiler`), pointed at manifest paths that would
break the Codex surface, or recommended a language and transport this server does
not use.

| Skill | Provenance | Why it diverges |
|-------|----------|-----------------|
| `cowork-plugin-customizer` | `anthropics/knowledge-work-plugins` | Cowork-only precondition moved into the description; its ad-hoc `zip` step conflicts with the byte-parity artifact gate |
| `create-cowork-plugin` | `anthropics/knowledge-work-plugins` | Unmodified |
| `find-skills` | `vercel-labs/skills` | Global `npx skills add -g` installs outside the repo and satisfies none of the three steps above |
| `mcp-builder` | **unrecorded** | Recommended TypeScript + streamable HTTP; this server is Python/FastMCP over stdio |
| `plugin-creator` | `openai/skills` | Wrote marketplace `source.path` as `./plugins/<name>`; the live path is `./plugin` |
| `plugin-settings` | `anthropics/claude-code` | Documents a `.local.md` pattern with no instances here; frontmatter name was Title Case |
| `plugin-structure` | `anthropics/claude-code` | Same Title Case issue; documents one manifest where three are enforced |
| `python-performance-optimization` | **unrecorded** | Profilers it requires are not installed, and the cost center here is `run_applescript()` IPC, not CPython |
| `reviewing-code` | **unrecorded** | Required `uv.lock` / `uv sync` / `prek`, none of which exist here |
| `testing-python` | **unrecorded** | Asserted `asyncio_mode = "auto"`; async tests here use `unittest.IsolatedAsyncioTestCase`, so a bare `async def test_` would silently skip |

**Re-syncing a vendored skill from upstream will discard these corrections.**
`computedHash` in `skills-lock.json` records the upstream source at pin time, not
the bytes of the local file, so it does not detect the divergence. Verified by
hashing every pinned file: none matched its recorded hash, including
`create-cowork-plugin`, which nobody had edited. Diff against upstream and
re-apply the repo-specific notes rather than overwriting.

**Two lock keys no longer match their skill's `name:`.** `skills-lock.json` is
keyed by the skill name as it stood upstream at pin time, so `plugin-settings`
and `plugin-structure` are still filed there under `Plugin Settings` and
`Plugin Structure`. Their local frontmatter was changed to the kebab-case form
that matches the directory, which is what every other skill here uses and what a
skill-name slug has to be. Look those two up by `skillPath`, not by name.
