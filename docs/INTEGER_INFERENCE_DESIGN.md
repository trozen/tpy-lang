# Integer Literal Default Type Policy

Status: Accepted design (Phase 1)

Decision date: 2026-02-16

## Summary

TurboPython must default unannotated integer literals to the configured default
integer type. The default configuration value is `Int32` (not `BigInt`) to
align with the project's primary goal of performance-first compiled output.

The compiler must provide an explicit escape hatch for CPython-like behavior:

- `--default-int=BigInt`

This policy is global per compiler invocation (all modules compiled together
in that run use the same default), to avoid mixed behavior across modules.

## Motivation

Current behavior defaults bare integer literals to `BigInt`:

```python
n = 4
for i in range(n):   # currently BigInt path
    ...
```

This is safe, but expensive for common cases such as loop bounds, counters,
and small configuration constants.

For performance-oriented codebases (for example HFT-like workloads), users want
predictable fixed-width integer behavior everywhere unless they explicitly opt
into `BigInt`.

## Policy

### 1. Global Default

Unannotated integer literals resolve to a configurable default integer type.

Supported values:

- `Int32` (new default)
- `Int64` (optional performance profile)
- `BigInt` (compatibility mode)

CLI:

```bash
tpyc --default-int=Int32 ...   # default behavior
tpyc --default-int=BigInt ...  # CPython-like integer behavior
tpyc --default-int=Int64 ...   # optional wider fixed-width default
```

### 2. Invocation-Wide Consistency

`--default-int` applies to the whole compilation invocation, not per module or
per function. This prevents a codebase from silently mixing overflow domains.

### 3. `int` Annotation Remains BigInt

The semantic meaning of explicit `int` does not change:

- `x: int` always means `BigInt`

Only unannotated integer literal deduction is controlled by `--default-int`.

### 4. Explicit Types Always Win

Explicit annotations/constructors override the default:

- `x: Int32 = 1` -> `Int32`
- `x: int = 1` -> `BigInt`
- `x = BigInt(1)` -> `BigInt`

### 5. Per-Module/Per-Function Overrides (Planned, Not Phase 1)

We expect to add local overrides, but they are out of scope for Phase 1.

Proposed syntax (subject to parser/directive design):

- Module-level directive:
  - `# tpy: default-int=Int32`
  - `# tpy: default-int=Int64`
  - `# tpy: default-int=BigInt`
- Function-level decorator:
  - `@tpy.config(default_int="Int32")`
  - `@tpy.config(default_int="Int64")`
  - `@tpy.config(default_int="BigInt")`

Proposed precedence (highest to lowest):

1. Function-level override (`@tpy.config(...)`)
2. Module-level directive (`# tpy: default-int=...`)
3. CLI flag (`--default-int=...`)
4. Compiler built-in default (`Int32`)

Phase 1 intentionally uses only levels 3 and 4.

## Semantics Matrix

| Code | `--default-int=Int32` | `--default-int=BigInt` |
|---|---|---|
| `x = 1` | `Int32` | `BigInt` |
| `for i in range(10)` | `Range<Int32>` | `Range<BigInt>` |
| `x: int = 1` | `BigInt` | `BigInt` |
| `x: Int32 = 1` | `Int32` | `Int32` |
| `x = int(1)` | `BigInt` | `BigInt` |

## Why This Over Deferred Literal Resolution First

An earlier design explored keeping `IntLiteralType` unresolved and deciding
type at each usage site. That remains useful, but it is Phase 2.

For now, we prioritize:

- deterministic, global behavior
- easy performance control from CLI
- no module-to-module surprises

Deferred usage-site resolution introduces more context sensitivity. It can be
added later as an optimization layer on top of this policy.

## Phase 1 Implementation Plan

1. Add CLI flag:
   - `--default-int=Int32|Int64|BigInt`
   - default value: `Int32`
2. Thread selected default into semantic analysis context.
3. Replace hardcoded `BigInt` literal default paths with selected default:
   - variable declaration defaulting
   - reassignment inference seed defaults
   - list/array literal integer element defaults where no stronger context exists
4. Keep explicit `int` semantics unchanged (`int` == `BigInt`).
5. Add tests for all three modes.
6. Update docs (`LANGUAGE_FEATURES.md`, README/CLI usage) in same change set.

## Compatibility and Migration

Changing default from `BigInt` to `Int32` can introduce overflow panics in
previously unbounded arithmetic where users relied on implicit BigInt.

Mitigation path:

1. Use `--default-int=BigInt` for compatibility runs.
2. Annotate selected values as `int` where unbounded behavior is required.
3. Keep performance-first default for new projects.

## Open Follow-Ups (Phase 2+)

1. Deferred `IntLiteralType` resolution for context-sensitive optimization.
2. Constant folding for pure integer expressions (`2 ** 16`, etc.).
3. Optional per-module/per-function policy override (`# tpy:` / decorators).
4. Policy interaction with future profiles (`@noalloc`, backend profiles).

## Phase 2 Proposal: Deferred IntLiteralType Resolution

This section preserves the earlier design direction for a later phase.

### Goal

Keep integer-literal-seeded locals as `IntLiteralType(value)` longer, then
resolve based on usage context instead of committing immediately.

Example intent:

```python
n = 4                    # IntLiteralType(4), not immediately concrete
for i in range(n):       # choose Int32/Int64 fast range path when safe
    ...
x = n + m                # may choose BigInt if arithmetic domain is ambiguous
```

### Proposed Mechanics

1. Keep `IntLiteralType` on eligible local variables (non-reassigned literal
   seeds) during semantic analysis.
2. Let usage sites constrain candidate concrete types:
   - `range(n)` can request fixed-width integer domain
   - explicit annotation/parameter type (`f(x: Int32)`) can request `Int32`
   - unconstrained arithmetic can force `BigInt` fallback for safety
3. Run a post-analysis resolution pass for remaining `IntLiteralType` vars.
4. Write resolved concrete types back into sema state used by codegen.

### Resolution Rules (Proposed)

1. If all usage constraints agree, choose that type.
2. If constraints disagree, fall back to `BigInt`.
3. If variable is unused, resolve to configured default type.
4. If variable is reassigned from non-literal/unknown source, resolve to
   configured default type (or `BigInt` in strict-safe mode).

Note: rule 3/4 should be aligned with whatever Phase 1 policy is active.

### Why Deferred (Not Phase 1)

- Adds context-sensitive typing behavior that is harder to reason about.
- Requires additional tracking of usage constraints and conflict diagnostics.
- Interacts with generic inference and overload selection in non-trivial ways.

Given current priorities, a global configurable default provides most of the
performance value with simpler and more predictable semantics.

### Interaction with Constant Folding

Deferred resolution benefits from preserving literal values. For example:

```python
for i in range(2 ** 16):  # fold to literal value to enable fixed-width path
    ...
```

If compile-time folding computes `2 ** 16` to a concrete literal, deferred
resolution can make the same decision as for `range(65536)`.
