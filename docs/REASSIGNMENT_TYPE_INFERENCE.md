# Reassignment-Based Local Type Inference

Status: In Progress

| Item | Status |
|---|---|
| `None -> Optional[T]` inference for unannotated reassignments | Done |
| Literal anchoring (`x=0; x=Int32(...)` -> `Int32` when range-safe) | Done |
| Function/method return-based anchoring (`x=None; x=make_point()`) | Done |
| Generic function return anchoring (`x=None; x=first(items)`) | Done |
| Method call anchoring (`x=None; x=obj.method()`) | Done |
| Chained call anchoring (`x=None; x=get_factory().create()`) | Done |
| `bool`/`Bool` annotation normalization | Done |
| Bare `x=None` without anchor emits inference error | Done |
| Late annotation retro-validation in local scopes | Done |
| Late annotation retro-validation for top-level globals | Done |
| Numeric lattice helper scaffolding for future numeric families | Done |
| Augmented assignment policy (`x=0; x += Int32(5)` does not anchor; emits warning) | Done |
| Numeric widening across reassignments (Int32->Int64, FixedInt->float, FixedInt->BigInt, unsigned->wider signed) | Done |
| Full lattice extension for remaining numeric families (`Float32`) | TODO |

This document defines how TurboPython should infer variable types across multiple
assignments when no explicit annotation is present.

Primary motivation:
- `x = None; x = Point()` should infer `x: Point | None`.
- Literal-first code should remain ergonomic (`x = 0; x = Int32(666)`).
- Inference must stay predictable and safe (no lifetime-sensitive surprises).

## Scope

Applies to:
- Unannotated local and module-level variable declarations parsed as `TpyVarDecl(name, None, init)`.
- Subsequent reassignments represented as repeated `TpyVarDecl` on the same name
  or `TpyAssign` to `TpyName`.

Does not change:
- Regular compatibility/coercion checks for explicitly annotated variables.
- Pointer/view coercion semantics.

## Core Rules

1. Infer from all writes to the same variable in its binding scope.
2. Compute a single inferred type using a deterministic LUB-style merge.
3. `None` participates as nullable lift: if non-None base type is `T`, result is `T | None`.
4. If no unique merge result exists, emit an error requiring annotation.

## Annotation Precedence and Retro-Validation

An explicit annotation on a later write becomes authoritative but must be
compatible with earlier writes.

- Valid:
  - `x = 0; x: Int32 = 666`
- Invalid:
  - `x = None; x: Point = Point()`
  - `x = 0.0; x: int = 100`

Behavior for invalid cases:
- Report error at the earlier incompatible assignment(s), since those writes are
  inconsistent with the final annotated type.

Rationale:
- Keeps explicit type declarations strict.
- Avoids silently "forgetting" earlier values.

## Merge Lattice (Inference-Time)

Inference uses a restricted merge lattice, not all compiler coercions.

### Always Allowed

1. `T + T -> T`
2. `None + T -> Optional[T]` (internally represented as nullable lift)
3. `Optional[T] + T -> Optional[T]`
4. `Optional[T] + Optional[T] -> Optional[T]`

### Numeric Family

Current numeric types in play:
- `IntLiteral` (internal)
- `int` (`BigInt`)
- `Int32`
- `float` (`FloatType`, future alias `Float64`)

Rules:
1. Literal-only integer chain defaults to `int` (`BigInt`).
   - `x = 1; x = 2` -> `int`
2. Integer literals can anchor to concrete integer types when present.
   - `x = 0; x = Int32(666)` -> `Int32` (if literals fit range)
3. Concrete integer + concrete integer uses widening, never narrowing.
   - `Int32 + int -> int`
4. Any mix with `float` widens to `float`.
   - `Int32 + float -> float`
   - `int + float -> float`
5. `None` may lift numeric result to optional.
   - `x = None; x = 123` -> `int | None`
6. Augmented assignment is not an anchoring operation.
   - `x = 0; x += Int32(5)` keeps `x` as `int` (`BigInt`) and emits a warning.
   - `x = 0; x += make_i32()` behaves the same when `make_i32() -> Int32`.
   - Explicit `int` anchors (`x: int = 0`, `x = int(0)`) suppress this warning.

### Bool

`Bool` is a separate family for inference and must not auto-merge with integer
or float families.

- `b = 0; b = True` -> error (requires explicit conversion/annotation)
- `b = bool(0); b = True` -> `Bool`

Parser/typing normalization goal:
- Treat `bool` and `Bool` annotation spellings as the same semantic type
  (`BOOL`) during analysis.

### Not Participating in v1 Inference Merge

These coercions are intentionally excluded from merge decisions:
- Record/pointer coercions (`T` <-> `Ptr[T]`, `ConstPtr[T]`)
- View coercions (`list/Array/StaticList` -> `Span`)
- Other coercions requiring lvalue/mutability/lifetime constraints

Reason:
- Keep inferred types independent from context-sensitive lifetime rules.

## Examples

### Valid

```python
x = None
x = Point()
# inferred: Point | None
```

```python
x = None
x = first([Int32(1), Int32(2)])
# inferred: Int32 | None
```

```python
x = None
x = get_factory().create()
# inferred: Product | None
```

```python
x = None
x = 123
# inferred: int | None
```

```python
x = 0
x = Int32(666)
# inferred: Int32
```

```python
x = 0
x: Int32 = 666
# resolved: Int32 (annotation authoritative, previous literal compatible)
```

### Invalid

```python
x = None
x: Point = Point()
# error: earlier assignment None incompatible with Point
```

```python
x = 0.0
x: int = 100
# error: earlier assignment 0.0 incompatible with int
```

```python
b = 0
b = True
# error: incompatible inference families (int vs Bool)
```

## Forward Compatibility: Additional Numeric Types

Planned types include `Int64`, `Int8`, `UInt32`, `Float32`, `Float64`.

To avoid rewriting inference later, implement numeric merging via metadata:
- kind: signed int / unsigned int / unbounded int / float
- width: bit width (or unbounded)
- rank: widening preference

Then merge by:
1. Same-kind widening for concrete numeric types.
2. Literal fit checks for anchoring.
3. Float dominance over integer families.
4. No narrowing between concrete non-literal types.

## Implementation Outline

### 1. Add a Sema Pre-Scan for Candidate Variables

Add a pre-pass in semantic analysis (function body + top-level block) that
collects assignment history per variable:
- write location
- rhs expression node
- rhs type (initially unresolved placeholder, then filled after expr analysis)
- whether the write had explicit annotation

This is conceptually similar to existing `scan_reassigned_vars` in codegen, but
must live in sema and track types/locations.

### 2. Analyze Writes and Resolve Authoritative Type

For each variable history:
1. If any annotated write exists, pick authoritative type from annotation
   (latest in source order), and retro-validate earlier writes against it.
2. Otherwise, fold writes with inference merge lattice.
3. On failure, emit deterministic diagnostic with conflicting types and source
   locations.

### 3. Integrate with Existing Statement Flow

In `StatementAnalyzer._analyze_var_decl`:
- Replace immediate "None cannot infer" behavior with lookup of pre-resolved
  inferred type when variable is in inference map.
- Preserve current coercion path (`compat.coerce_expr`) once target type is
  known.

In `StatementAnalyzer._analyze_assign`:
- Keep existing assignment validation, but ensure target type reflects
  pre-resolved inferred/authoritative type.

### 4. Diagnostics

New errors should include:
- Variable name
- Incompatible types
- Write locations of conflict
- "add explicit annotation" fallback guidance when no merge exists

### 5. Tests

Add snippet tests covering:
1. `None -> T` and `None -> int literal`
2. literal anchoring to `Int32`
3. int/float widening
4. bool/numeric mismatch
5. late-annotation valid and invalid retro-check cases
6. optional annotation compatibility

No snapshot updates for existing tests should be done without explicit review if
behavior changes.

## Non-Goals (This Iteration)

- Pointer-aware merge inference
- Dataflow narrowing (`if x is not None` branch refinement)
- Cross-branch phi-style advanced merge beyond current assignment history rules
- Protocol-based inferred variable types

## Resolved Decisions

1. Multiple explicit annotations for the same variable must not conflict.
   - If annotated writes disagree, emit an error.
2. Retro-validation reports first failure only (for now).
   - Multi-diagnostic reporting can be added later.
3. Future unsigned anchoring rules are strict:
   - Negative literals never anchor to unsigned integer types.
   - Anchoring to fixed-width unsigned types requires literal range fit.
   - Inference never performs implicit narrowing across concrete numeric types.
