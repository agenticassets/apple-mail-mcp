---
name: testing-python
description: Write and evaluate effective Python tests using pytest. Use when writing tests, reviewing test code, debugging test failures, or improving test coverage. Covers test design, fixtures, parameterization, mocking, and async testing.
---

# Writing Effective Python Tests

## Core Principles

Every test should be **atomic**, **self-contained**, and test **single functionality**. A test that tests multiple things is harder to debug and maintain.

## Test Structure

### Atomic unit tests

Each test should verify a single behavior. The test name should tell you what's broken when it fails. Multiple assertions are fine when they all verify the same behavior.

```python
# Good: Name tells you what's broken
def test_user_creation_sets_defaults():
    user = User(name="Alice")
    assert user.role == "member"
    assert user.id is not None
    assert user.created_at is not None

# Bad: If this fails, what behavior is broken?
def test_user():
    user = User(name="Alice")
    assert user.role == "member"
    user.promote()
    assert user.role == "admin"
    assert user.can_delete_others()
```

### Use parameterization for variations of the same concept

```python
import pytest

@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("World", "WORLD"),
    ("", ""),
    ("123", "123"),
])
def test_uppercase_conversion(input, expected):
    assert input.upper() == expected
```

### Use separate tests for different functionality

Don't parameterize unrelated behaviors. If the test logic differs, write separate tests.

## Rules for this repo (apple-mail-mcp)

[`tests/CLAUDE.md`](../../../tests/CLAUDE.md) is canonical for this suite; read it before adding a
test module. The points below are the ones people most often get wrong.

### Async tests use `unittest.IsolatedAsyncioTestCase`

There is **no** `pytest-asyncio` and **no** `asyncio_mode` setting in this repo, so a bare
module-level `async def test_...` is collected and then skipped with a warning — it never runs
its assertions. Subclass `unittest.IsolatedAsyncioTestCase` instead:

```python
import unittest

class DashboardAccountScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_passes_account_through(self):
        ...
```

`anyio` is present only as a transitive `fastmcp` dependency, not as a test plugin.

### Imports at module level

Put ALL imports at the top of the file:

```python
# Correct
import unittest
from unittest.mock import patch

from apple_mail_mcp.tools import inbox as inbox_tools

def test_something():
    ...

# Wrong - no local imports
def test_something():
    from apple_mail_mcp.tools import inbox  # Don't do this
```

### Mock AppleScript at the boundary, not the FastMCP transport

Tests here do **not** drive the server through a `fastmcp.client.Client`; they call the tool
functions directly and mock the AppleScript boundary. Two established patterns:

```python
# Capture the generated script: patch subprocess.run and read kwargs["input"]
with patch("subprocess.run", side_effect=capture) as mock_run:
    ...

# Or patch run_applescript in the module under test
with patch("apple_mail_mcp.tools.inbox.run_applescript", return_value=payload):
    ...
```

Templates live in `tests/cross_cutting/test_modernization_3_1_5.py` (`_ScriptCapture`),
`tests/search/test_mail_search_tools.py`, and `tests/compose/test_compose_tools.py`.
Local CI-equivalent gates never launch Mail.app.

### Fixtures must be synthetic — this repo is PUBLIC

Use `sender@example.com` and invented subjects. Never build a fixture by pasting a real
message, header block, `Message-ID`, or account UUID out of a live run, even when reproducing
a real bug: reduce it to the shape that triggers the bug.

### Test count is single-sourced

The collected-test count lives only in `tools/expected_test_count.txt`. After adding or removing
tests, recount and update that one file — the dev-check/release gate fails on drift and prints
the new number:

```bash
PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests
```

Do not scatter counts through prose docs.

## Fixtures

### Prefer function-scoped fixtures

```python
@pytest.fixture
def client():
    return Client()

async def test_with_client(client):
    result = await client.ping()
    assert result is not None
```

### Use `tmp_path` for file operations

```python
def test_file_writing(tmp_path):
    file = tmp_path / "test.txt"
    file.write_text("content")
    assert file.read_text() == "content"
```

## Mocking

### Mock at the boundary

```python
from unittest.mock import patch, AsyncMock

async def test_external_api_call():
    with patch("mymodule.external_client.fetch", new_callable=AsyncMock) as mock:
        mock.return_value = {"data": "test"}
        result = await my_function()
        assert result == {"data": "test"}
```

### Don't mock what you own

Test your code with real implementations when possible. Mock external services, not internal classes.

## Test Naming

Use descriptive names that explain the scenario:

```python
# Good
def test_login_fails_with_invalid_password():
def test_user_can_update_own_profile():
def test_admin_can_delete_any_user():

# Bad
def test_login():
def test_update():
def test_delete():
```

## Error Testing

```python
import pytest

def test_raises_on_invalid_input():
    with pytest.raises(ValueError, match="must be positive"):
        calculate(-1)

async def test_async_raises():
    with pytest.raises(ConnectionError):
        await connect_to_invalid_host()
```

## Running Tests

Use the repo venv (`.venv/`, editable install). There is no `uv` and no `pytest-xdist` here, so
`uv run` and `-n auto` both fail:

```bash
.venv/bin/pytest tests/                 # full suite
.venv/bin/pytest tests/ -x              # stop on first failure
.venv/bin/pytest tests/cli/test_cli.py  # specific file
.venv/bin/pytest -k "test_name"         # tests matching pattern
bash tools/gates/dev-check.sh           # manifests + module budget + pytest + test-count gate
```

## Checklist

Before submitting tests:
- [ ] Each test tests one thing
- [ ] Async tests subclass `unittest.IsolatedAsyncioTestCase` (no bare `async def test_`)
- [ ] Imports at module level
- [ ] Descriptive test names
- [ ] AppleScript mocked at the boundary; no test launches Mail.app
- [ ] Fixtures are synthetic (`sender@example.com`) — no real mail data
- [ ] `tools/expected_test_count.txt` updated if the collected count changed
- [ ] Parameterization for variations of same behavior
- [ ] Separate tests for different behaviors
