# Integer Literal Default Type Policy

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Global `--default-int` CLI flag, configurable default (int32/int64/BigInt), explicit `int` stays BigInt | Done |
| **Phase 2a** | Retro-widening of literal-seeded function locals at typed-slot uses | Replaced by Phase 2b (uses never decide a type) |
| **Phase 2b** | Pending integer locals: a literal-seeded function local is typed by the values stored in it, decided once the function is analyzed | Done (function locals; module globals and collection elements not covered) |
| **Future** | Per-module/per-function overrides (`# tpy:` directives, `@tpy.config`), constant folding | Planned |

Decision date: 2026-02-16

## Summary

TurboPython must default unannotated integer literals to the configured default
integer type. The default configuration value is `int32` (not `BigInt`) to
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

For performance-oriented codebases (for example latency-sensitive workloads), users want
predictable fixed-width integer behavior everywhere unless they explicitly opt
into `BigInt`.

## Policy

### 1. Global Default

Unannotated integer literals resolve to a configurable default integer type.

Supported values:

- `int32` (new default)
- `int64` (optional performance profile)
- `BigInt` (compatibility mode)

CLI:

```bash
tpy --default-int=int32 ...    # default behavior
tpy --default-int=BigInt ...   # CPython-like integer behavior
tpy --default-int=int64 ...    # optional wider fixed-width default
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

- `x: int32 = 1` -> `int32`
- `x: int = 1` -> `BigInt`
- `x = BigInt(1)` -> `BigInt`

### 5. Per-Module/Per-Function Overrides (Planned, Not Phase 1)

We expect to add local overrides, but they are out of scope for Phase 1.

Proposed syntax (subject to parser/directive design):

- Module-level directive:
  - `# tpy: default-int=int32`
  - `# tpy: default-int=int64`
  - `# tpy: default-int=BigInt`
- Function-level decorator:
  - `@tpy.config(default_int="int32")`
  - `@tpy.config(default_int="int64")`
  - `@tpy.config(default_int="BigInt")`

Proposed precedence (highest to lowest):

1. Function-level override (`@tpy.config(...)`)
2. Module-level directive (`# tpy: default-int=...`)
3. CLI flag (`--default-int=...`)
4. Compiler built-in default (`int32`)

Phase 1 intentionally uses only levels 3 and 4.

## Semantics Matrix

| Code | `--default-int=int32` | `--default-int=BigInt` |
|---|---|---|
| `x = 1` | `int32` | `BigInt` |
| `for i in range(10)` | `Range<int32>` | `Range<BigInt>` |
| `x: int = 1` | `BigInt` | `BigInt` |
| `x: int32 = 1` | `int32` | `int32` |
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
   - `--default-int=int32|int64|BigInt`
   - default value: `int32`
2. Thread selected default into semantic analysis context.
3. Replace hardcoded `BigInt` literal default paths with selected default:
   - variable declaration defaulting
   - reassignment inference seed defaults
   - list/array literal integer element defaults where no stronger context exists
4. Keep explicit `int` semantics unchanged (`int` == `BigInt`).
5. Add tests for all three modes.
6. Update docs (`LANGUAGE_FEATURES.md`, README/CLI usage) in same change set.

## Compatibility and Migration

Changing default from `BigInt` to `int32` can introduce overflow panics in
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

## Phase 2a: Literal-Seeded Local Retro-Widening (Replaced)

A local seeded by a literal took the default int at once and was re-typed
to a fixed-int target at its first typed-slot use (`offset = 0; f(offset)`
with a `uint64` parameter). Its earlier reads stayed typed at the old
width, which miscompiled every operation over them, and the use deciding
the type made the verdict order-dependent. Phase 2b replaced it: uses never
decide a type.

## Phase 2b: Pending Integer Locals (Done)

An unannotated function local whose first binding is a bare integer
literal (`scan_pending_num_locals`, `tpyc/prescan.py`) is PENDING: it is
the default int widened by the typed values stored in it -- plain and
augmented assignments, joined over the whole set of stores by the slot
widening relation -- and every use, earlier ones included, is compiled at
that type. It never becomes unsigned (a literal counts as the default
int), and a literal the default does not hold makes it `int`. A local
whose first binding is a typed value -- a numeric type constructor call
(`x = int8(3)`) included -- keeps that type and refuses a wider value.
The sibling arms of one `if` / `match` / `try` that each bind a local are
together its first binding (`scan_first_bindings`): typed arms join, and
beside a bare-literal arm the family DEFAULT must widen into the typed
arms' join (and the literal must fit it), else it is an error.

The same machinery serves float locals, with `float` as the float
literal's default. Nothing is analyzed twice (`tpyc/sema/pending_num.py`):

- one cell per pending local on the context, holding the types stored in
  it; a local first bound from a pending value (`j = steps + 1`) gets a
  derived cell typed by its first store;
- a read of a pending local is typed `PendingNumType` (the join of cells
  and a concrete floor), and only a consumer that asked for one -- keyed
  on the node it analyzes at the two expression entry points -- receives
  it: an operator operand, a store, a return, a yield, `print`, an
  f-string value, a builtin-sequence index or slice bound, an argument to a
  declared numeric parameter of a non-generic, non-overloaded callee, a
  field write, an annotated initializer, `+=` on a declared slot;
- any other consumer SETTLES the local from the stores seen so far and
  freezes it; a later wider store is refused naming that use (the
  documented order dependence);
- an operator over a pending operand is typed at once (arithmetic and
  bitwise: the wider operand, itself pending; `/`: float; comparisons:
  bool; shifts: the left operand) and resolved through the ordinary
  `OperatorResolver` once its operands settle; a conversion whose ends are
  not known yet is a `TpyCoerce` placeholder, filled then or spliced out of
  the tree;
- a lambda, nested def or generator expression settles the locals it reads
  in the owning function before its body is analyzed;
- at the end of the function body (`LocalTypeDeduction.resolve_all`) every
  cell settles (a fixed point over locals stored from one another), every
  deferred operation resolves, and no pending type remains in any table.

Module globals and collection-element literals keep the Phase 1 policy.

### Interaction with Constant Folding

Deferred resolution benefits from preserving literal values. For example:

```python
for i in range(2 ** 16):  # fold to literal value to enable fixed-width path
    ...
```

If compile-time folding computes `2 ** 16` to a concrete literal, deferred
resolution can make the same decision as for `range(65536)`.
