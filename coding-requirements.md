# Generic Coding Requirements — Python-Focused Project Style Guide

> Version: 1.0

This document captures development-only coding preferences and hard constraints for new projects, primarily Python-based. It is intended to be used while authoring, reviewing, and refactoring code.

---

## ⚠️ CRITICAL: Git Operations Policy

**AGENT MUST NEVER COMMIT OR PUSH WITHOUT EXPLICIT USER AUTHORIZATION.**

### Agents must NEVER:

- Run `git commit` without explicit user request
- Run `git push` or `git push --force` without explicit user authorization
- Run history-rewriting tools (git filter-repo, git rebase -i, etc.) without explicit approval

### If a git action is proposed:

1. STOP and ask for explicit confirmation
2. Explain what will be changed, what will be lost, and impact on branches/remotes
3. WAIT for unambiguous user approval before proceeding
4. Never execute `git push` or `git push --force` as part of an automated sequence

Failure to follow this constraint results in permanent loss of uncommitted changes, rewrites shared history, and violates user autonomy.

---

## Table of Contents

- **Hard Constraints**
- **Typing & Data-Shape Standards**
- **Constants & Magic Values**
- **DRY & Module Organization**
- **Small, Composable Functions**
- **Validation & Invariants**
- **Testing Expectations**
- **Error Handling & Logging**
- **Performance & Correctness**
- **Linting & Build Hygiene**
- **Deliverables & Checklist**
- **Dependency Management**

---

## **Hard Constraints (non-negotiable)**

- No escape-hatch types (e.g., untyped dict soup, `Any` without justification).
  - If the shape is unclear, define an explicit `TypedDict`, `dataclass`, or `Protocol`.
- No `# type: ignore[...]` or `# type: ignore` comments.
  - Fix the underlying type issue instead of suppressing it.
  - If a library lacks type hints, vendor a `.pyi` stub file or import with a cast.
- No unused variables/imports or dead code.
  - Python: unused imports are forbidden. Use tools like `autoflake` or `ruff` to catch them.
- All imports must be declared at the top of the file.
  - Do not use function-scoped or lazy imports (e.g., `import X` inside a function).
- No copy/paste repetition — refactor repeated logic into helpers.
- Replace hard-coded strings/keys with constants/enums where applicable.
- Keep changes testable; do not add or update unit tests unless explicitly requested.
- No infinite loops; prefer bounded retries or cancellable constructs.
- Use type hints on all function signatures and module-level variables.
  - Python 3.10+: use `|` for unions instead of `Union[A, B]`.
  - Use `Optional[T]` or `T | None` for nullable types, not bare `None`.

---

## **Typing & Data-Shape Standards**

- Prefer explicit domain types over generic blobs.
- Use discriminated unions / tagged variants for modes/kinds.
- Avoid "stringly-typed" APIs; prefer typed constants/enums.
- Keep narrow types at module boundaries to prevent invalid states.
- Python dicts that will be reassigned must have explicit type annotations.

Bad (Python):

```py
error_msg = {"msg": None}  # locks type to None
error_msg["msg"] = "error"  # type error
```

Good (Python):

```py
error_msg: dict[str, str | None] = {"msg": None}
error_msg["msg"] = "error"
```

Example (discriminated union):

```py
from typing import Literal

Result[T] = (
    dict[Literal['kind'], Literal['ok']] | dict[Literal['value'], T]
    | dict[Literal['kind'], Literal['err']] | dict[Literal['code'], str] | dict[Literal['message'], str]
)
```

When calling third-party APIs that return callables, call the method and validate the returned type before parsing.

Bad:

```py
resp_text = response.text  # may be a method object
json.loads(resp_text)
```

Good:

```py
resp_text: str = response.text()
json.loads(resp_text)
```

---

## **Constants over Magic Strings and Numbers**

- Replace repeated literal strings with constants.
- Use `Enum` or module-level constants for known sets of values.
- Replace magic numbers with named constants.

Example:

```py
from enum import Enum

class Status(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"

MAX_RETRIES = 3
TIMEOUT_SECONDS = 30
```

---

## **DRY: Prefer Helpers**

- Avoid long if/else chains—use `switch`-like dicts, handler maps, or strategy objects.
- Extract common utilities into focused helper modules.

### Recommended Module Organization (for larger projects)

1. `<domain>_consts.py` — ALL named constants, enums
2. `<domain>_types.py` — ALL type definitions, TypedDicts, dataclasses, Protocols
3. `<domain>_helpers.py` — Reusable pure functions operating on types

Do NOT mix constants/types/helpers across those files.

### Utility Function Modules

- `string_helpers.py` — string normalization/comparison
- `math_helpers.py` — numeric calculations and RNG
- `date_helpers.py` — date/time utilities
- Keep concerns separated: numeric logic in math, string logic in string.

---

## **Small, Composable Functions**

- Prefer small single-purpose functions that are easy to unit test.
- When functions grow, split into extraction, transform, validation, formatting.
- Keep core logic pure; isolate side effects.
- Avoid long parameter lists; use TypedDict or dataclass for related parameters.

---

## **Validation & Invariants (Fail Fast)**

- Enforce invariants centrally via validators/assertions.
- Add runtime checks (dev/debug) and, where requested, unit tests.

Example assert (Python):

```py
def assert_positive_integer(name: str, value: int) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer; received {value}")
    return value
```

---

## **Testing Expectations (on request)**

- Add/update unit tests only when explicitly requested.
- For stochastic pipelines include deterministic/seeded tests.
- Tests should be deterministic and focused on pure logic.
- Use `pytest` as the standard test runner.

---

## **Error Handling & Logging**

- Prefer structured/typed errors over string parsing.
  - Define custom exception classes for domain-specific errors.
- Log enough context (ids, inputs) without noise.
- Do not swallow errors; surface or handle them clearly.
- Use `logging` module with structured output; avoid print statements in library code.

Example:

```py
import logging

logger = logging.getLogger(__name__)

class ValidationError(Exception):
    """Raised when input validation fails."""
    pass

try:
    result = process_data(data)
except ValidationError as e:
    logger.error(f"Validation failed: {e}", extra={"data": data})
    raise
```

---

## **Performance & Correctness**

- Correctness first. Avoid expensive work inside hot loops.
- Profile before optimizing; don't guess at bottlenecks.
- Use generators for large sequences; avoid materializing unnecessary lists.

---

## **Linting & Code Quality (required)**

- Use the following tools as your standard (adjust based on project setup):
  - **Type checking**: `mypy` or `pyright` — strict mode
  - **Linting**: `ruff` (fast, comprehensive) or `flake8`
  - **Code formatting**: `black` (opinionated, deterministic)
  - **Import sorting**: `isort`
  - **Unused imports/vars**: `autoflake` or `ruff` with `unused-imports` rule

Before finishing changes:

1. Run `mypy --strict` or `pyright` and ensure no errors
2. Run `ruff check .` (or equivalent linter)
3. Run `black .` to auto-format (or ensure it passes `--check`)
4. Run `isort .` if used (or ensure it passes `--check`)

Example pre-commit config:

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 24.1.0
    hooks:
      - id: black
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
        args: [--fix]
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.8.0
    hooks:
      - id: mypy
        args: [--strict]
```

---

## **Compile/Build Hygiene (required)**

Before finishing, run the repo's standard validation commands and ensure they pass.
For Python projects, typical commands are:

```bash
# Type checking
mypy --strict src/
# or
pyright src/

# Linting
ruff check src/

# Formatting check
black --check src/

# Compile check
python -m compileall src/

# Tests (if applicable)
pytest tests/
```

---

## **What to Deliver With Each Change**

- Short implementation note: what changed, where, and why.
- How to validate: exact commands to run and expected output.
- Tests added/updated (only if requested) and what they cover.
- Any new configuration or environment variables documented with defaults and rationale.

---

## **Checklist (self-verify before finishing)**

- [ ] No escape-hatch types used (e.g., untyped dicts, bare `Any`)
- [ ] No unused variables/imports
- [ ] No duplicated logic left unrefactored
- [ ] Magic strings/numbers replaced with named constants/enums
- [ ] Invariants validated centrally
- [ ] No new/updated test files unless explicitly requested
- [ ] If tests added for randomness, deterministic coverage exists
- [ ] All functions have type hints on parameters and return types
- [ ] Type checker passes (`mypy --strict`, `pyright`)
- [ ] Linter passes (`ruff check`)
- [ ] Code formatting passes (`black --check`)
- [ ] Code compiles/runs successfully using repo-standard commands
- [ ] Clear validation steps provided
- [ ] **NO commits made without explicit user authorization**
- [ ] **NO pushes made to remote**

---

## **File Migrations & Refactoring**

- When moving/renaming files, update all imports in one batch.
- Verify imports are resolvable via `python -c "import your_module"` or similar.
- Delete old file only after verifying no consumers remain.
- Use `grep -r` to find remaining references before deletion.

---

## **Dependency Management (pip/PDM/Poetry)**

Choose one and stick with it:

### PDM (recommended for this workspace)

- Use `pdm install`, `pdm add`, `pdm remove` for dependency operations.
- `pdm.lock` is the canonical lock file.
- Use the venv managed by PDM (usually in `.venv/`).

### pip + requirements.txt

- Use `pip install -r requirements.txt` for reproducible installs.
- Pin versions: `package==1.2.3` (not `package>=1.2.3`).
- Keep `requirements-dev.txt` for development-only dependencies.

### Poetry

- Use `poetry add`, `poetry remove` for dependency operations.
- `poetry.lock` is the canonical lock file.

---

## **Ways to use fewer tokens when working with Claude**

**1. Switch models strategically**
Start every session on Sonnet. Only switch to Opus when you genuinely need deep analysis or complex refactoring. Drop to Haiku for mechanical stuff like quick lookups, formatting, or renaming.

**2. Lower the effort level**
Opus 4.8 has four effort levels — Low, Medium, High (default), and Max. Use "medium" for straightforward tasks.

**3. Use `/compact` when context grows**
Use `/compact` when context grows large to compress history while preserving key information.

**4. Keep your CLAUDE.md lean**
Every word in your CLAUDE.md is injected into every prompt, so remove outdated sections.

**5. Start fresh conversations for unrelated tasks**
A conversation about auth shouldn't carry context from a database debugging session.

**6. Watch out for file search token costs**
Being specific in your prompts about which files to look at reduces unnecessary reads.

---

_End of file._
