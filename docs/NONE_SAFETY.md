# None Safety

This document describes TurboPython's `T | None` safety behavior, current limitations, and planned work.

## Status

| Item | Status | Notes |
|---|---|---|
| Parse `assert` statements | Done | Supports `assert cond` and `assert cond, "literal"` |
| `assert`-driven Optional narrowing | Done | `assert x is not None` narrows `x` after assertion |
| Warning + runtime check for Optional field/method/subscript access | Done | Applies when Optional may be `None` |
| Elide runtime checks when non-None is proven | Done | Via `if x is not None` and `assert x is not None` |
| `is None` / `is not None` on value optionals | Done | Uses `std::optional<T>::has_value()` in generated code |
| Optional-aware identity checks after flow narrowing | Done | Avoids invalid `== nullptr` for value optionals |
| Optional in value-consuming operators without proof (`x + 1`) | Done | Warns and inserts runtime null checks |
| Generalized flow facts beyond name-based narrowing | Todo | Current facts track variable names |
| Rich assert messages (non-literal expressions) | Todo | Currently string-literal-only |

## Test Coverage & Blockers

| Area | Coverage | Blocking condition |
|---|---|---|
| Assert narrowing (`assert x is not None`) | Covered | None |
| Compound assert narrowing (`assert x is not None and ...`) | Covered | None |
| Reassignment invalidates narrowing | Covered | None |
| Guard-clause narrowing (`if x is None: return`) | Covered | None |
| Else-branch narrowing | Covered | None |
| Non-None assignment proves non-None | Covered | None |
| Truthiness assert does not narrow (`assert x`) | Covered (current behavior guard) | None |
| Branch merge intersection | Covered | None |
| Mixed-variable narrowing isolation | Covered | None |
| Negation narrowing (`not (x is None)`) | Covered | None |
| Function-boundary isolation | Covered | None |
| Operator-phase runtime checks (`+`, comparisons, unary on Optionals) | Covered | Runtime panic tests for binop/comparison/unary optionals |
| Proven optional comparison unwrapping (`(*x) > 0` vs `x > 0`) | Covered | Dedicated post-assert comparison test |
| Loop-specific narrowing stress (`while` + reassignment/continue/break) | Partial | Need dedicated loop-focused semantics cases |
| REPL-specific regression suite | Not covered | No dedicated REPL test harness in snippet suite |
| Rich assert message forms | Not covered | Non-literal assert message support not implemented |

## Goals

- Keep code idiomatic and migration-friendly.
- Prevent undefined behavior in generated C++.
- Let users opt into explicit proofs (`if x is not None`, `assert x is not None`) to remove runtime checks.

## Current Behavior

### Non-value optionals (`Point | None`, `list[T] | None`, ...)

- Unproven field/method/subscript access compiles with a warning and inserts a runtime null check.
- Proven non-None access (via guard or assert) does not insert that check.

Example:

```python
def f(p: Point | None) -> Int32:
    return p.mag()   # warning + runtime null check
```

```python
def g(p: Point | None) -> Int32:
    if p is not None:
        return p.mag()  # no warning, no extra null check
    return -1
```

### Value optionals (`Int32 | None`, `Bool | None`, `float | None`)

- `is None` / `is not None` checks work.
- `== None` / `!= None` on optionals is rejected; use `is None` / `is not None`.
- Unproven value-consuming operator use compiles with a warning and inserts runtime checks.
- Proven non-None operator use (guard/assert) emits unchecked unwraps with no extra check.

### Narrowing sources

- `if x is not None:`
- `if x is None: ... else:`
- short-circuit condition flow (`and` / `or`)
- `assert x is not None`

## Assert

Supported:

- `assert cond`
- `assert cond, "message"`

Behavior:

- At runtime: panics if condition is false.
- In sema: contributes narrowing facts after the assertion.

Current limitation:

- assert message must be a string literal.

## Diagnostics

Unproven optional access emits:

- warning about potential None access
- note that generated code adds runtime null check
- guidance to use guard/assert to prove non-None

## Known Limitations

- Narrowing is currently name-based in flow analysis.
- Equality/ordering rules across mixed Optional/non-Optional values are still conservative.
  - Current focus is safety (runtime checks) and explicit `is`/`is not` for None identity.
  - Broader Python-compat comparison semantics remain open.

## Planned (Next)

- Broaden and refine narrowing coverage where needed.
