---
name: reviewing-code
description: Review code for quality, maintainability, and correctness. Use when reviewing pull requests, evaluating code changes, or providing feedback on implementations. Focuses on API design, patterns, and actionable feedback.
---

# Code Review

## Philosophy

Code review maintains a healthy codebase while helping contributors succeed. The burden of proof is on the PR to demonstrate it adds value. Your job is to help it get there through actionable feedback.

**Critical**: A perfectly written PR that adds unwanted functionality must still be rejected. The code must advance the codebase in the intended direction. When rejecting, provide clear guidance on how to align with project goals.

Be friendly and welcoming while maintaining high standards. Call out what works well. When code needs improvement, be specific about why and how to fix it.

## What to Focus On

### Does this advance the codebase correctly?

Even perfect code for unwanted features should be rejected.

### Dependency version compatibility

When a PR adapts code to a new version of a dependency (e.g., removing a parameter that was dropped upstream, using a new API):
- **The version pin in `pyproject.toml` must match.** If the change breaks compatibility with the previously-pinned minimum version, the minimum version must be bumped. Otherwise users on the old version get a regression.
- **If backwards compatibility with the old version is desired**, the code must handle both versions (e.g., try/except, version check). Simply deleting the old API usage without bumping the pin is always wrong — it silently breaks users on the old version.
- **Dependency edits should be scoped to the PR's purpose.** This repo has no lock file — `pyproject.toml` is the only pin — so an unrelated bump there is pure diff noise. `fastmcp` is pinned to an exact version; moving that pin is a behavior change needing its own justification and a `dev-check.sh release` run, not a drive-by edit.

### API design and naming

Identify confusing patterns or non-idiomatic code:
- Parameter values that contradict defaults
- Mutable default arguments
- Unclear naming that will confuse future readers
- Inconsistent patterns with the rest of the codebase

### Specific improvements

Provide actionable feedback, not generic observations.

### User ergonomics

Think about the API from a user's perspective. Is it intuitive? What's the learning curve?

## For Agent Reviewers

1. **Read the full context**: Examine related files, tests, and documentation before reviewing
2. **Check against established patterns**: Look for consistency with codebase conventions
3. **Verify functionality claims**: Understand what the code actually does, not just what it claims
4. **Consider edge cases**: Think through error conditions and boundary scenarios

## What to Avoid

- Generic feedback without specifics
- Hypothetical problems unlikely to occur
- Nitpicking organizational choices without strong reason
- Summarizing what the PR already describes
- Star ratings or excessive emojis
- Bikeshedding style preferences when functionality is correct
- Requesting changes without suggesting solutions
- Focusing on personal coding style over project conventions

## Tone

- Acknowledge good decisions: "This API design is clean"
- Be direct but respectful
- Explain impact: "This will confuse users because..."
- Remember: Someone else maintains this code forever

## Decision Framework

Before approving, ask:

1. Does this PR achieve its stated purpose?
2. Is that purpose aligned with where the codebase should go?
3. Would I be comfortable maintaining this code?
4. Have I actually understood what it does, not just what it claims?
5. Does this change introduce technical debt?

If something needs work, your review should help it get there through specific, actionable feedback. If it's solving the wrong problem, say so clearly.

## Comment Examples

**Good comments:**

| Instead of | Write |
|------------|-------|
| "Add more tests" | "The `handle_timeout` method needs tests for the edge case where timeout=0" |
| "This API is confusing" | "The parameter name `data` is ambiguous - consider `message_content` to match the MCP specification" |
| "This could be better" | "This approach works but creates a circular dependency. Consider moving the validation to `utils/validators.py`" |

## Checklist

Before approving, verify:

- [ ] Local gates green: `bash tools/gates/dev-check.sh` (release tier before shipping)
- [ ] Changes align with repository patterns and conventions
- [ ] API changes are documented and backwards-compatible where possible
- [ ] Error handling follows project patterns (specific exception types)
- [ ] Tests cover new functionality and edge cases
- [ ] The change advances the codebase in the intended direction
