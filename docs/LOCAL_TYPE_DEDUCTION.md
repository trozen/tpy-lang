# Local Variable Type Deduction -- Unified Post-Body Pass

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Unify infrastructure: replace ListLiteralTracker, StrVarTracker, and deduction parts of ReassignmentInference with single LocalTypeDeduction class. Preserve existing behavior. | Done |
| 2a | Numeric widening across assignments (int->float, Int32->Int64, unsigned->wider signed). | Done |
| 2b | Different-size list reassignment, return-type-driven deduction, alias propagation for lists. | Done |
| 2c | Cross-variable list reassignment (`a = [1,2,3]; b = [4,5]; a = b` -- both should become list). | Done |
| 3 | Narrowing integration: deduced `Optional[T]` variables work with `if x is not None` narrowing. | Done |
| 4 | String deduction test coverage: dedicated tests for StrView-vs-str resolution and string alias propagation. | Done |
| 5a | Empty list inference: `xs = []; xs.append(v)` and `xs = list(); xs.append(v)` infer element type from `.append()`/`.insert()` usage, with numeric widening and alias propagation. | Done |
| 5b | Empty dict inference: `d = {}; d[k] = v` and `d = dict()` infer key/value types from subsequent subscript assignment. | Done |
| 5c | Empty set inference: `s = set(); s.add(v)` infers element type from subsequent `.add()` calls. | Done |
| 5d | Unify empty container inference: extract shared helpers for list/dict/set (PENDING_CONTAINER_TYPES constant, unified container lookup, shared resolution epilogue, merged param context tracking). Single code paths prevent forgetting one container type. | Done |
| 6 | List element-type widening: `.append(Int64)` on `[1,2]` widens element type from Int32 to Int64. Already handled by `_widen_inferred_type` in Phase 5a infrastructure; added test coverage. | Done |
| 7 | Deferred generic instance inference: `x = GenericType()` with unresolved type params, resolved from subsequent method calls via constraint unification. Reuses `match_type_with_inference` from bidirectional inference. See `BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md`. | Not started |
| 8 | State ownership: move deduction-related fields from SemanticContext into sub-structures owned by LocalTypeDeduction. | Not started |

## Future Extensions (post-1.0)

| Extension | Description |
|-----------|-------------|
| Per-assignment-segment typing | SSA-style reasoning: each assignment to a variable creates a new "version" with its own type. Enables narrower types per segment (e.g. StrView before reassignment, str after), avoiding unnecessary allocations. Requires liveness/escape analysis. See details at end of document. |
| Empty list to Array promotion | `xs = []; xs.append(1); xs.append(2)` could resolve to `Array[Int32, 2]` if the final size is statically known (no dynamic mutations like loop appends or pop/remove). Would need to compute max required size from constant append/insert/extend counts. Likely low priority -- in hot paths users would declare `Array` explicitly with a known max size. |

## Motivation

The compiler currently has three independent mechanisms for deducing local
variable types after analyzing a function body:

1. **ReassignmentInference** -- handles `None + T -> Optional[T]` and int
   literal range refinement (runs inline during analysis)
2. **ListLiteralTracker** -- resolves `PendingListType` to `Array` vs `list`
3. **StrVarTracker** -- resolves `PendingStrType` to `StrView` vs `str`

These share the same lifecycle (collect usage facts during analysis, resolve
after body) but are completely independent. They duplicate infrastructure
(counters, tracking dicts, cleanup, call sites) and cannot coordinate --
e.g. reassignment inference cannot see whether a list resolved to Array or
list.

This document proposes replacing all three with a single unified post-body
deduction pass.

## Scope

The unified pass replaces **post-body resolution only**. Simple cases where
a variable has a single initializer with an unambiguous type (e.g.
`x = get_int32()`) continue to be resolved inline during analysis -- there
is nothing to defer.

The pass kicks in when a variable's type cannot be determined from its
initializer alone:

- Multiple assignments with different types
- Pending container kind (Array vs list)
- Pending string ownership (StrView vs str)
- None-seeded variables
- Int-literal-seeded variables
- Type influenced by usage (parameter passing, return)

Explicit type annotations always take precedence and are never overridden
by deduction.

## Design

### Phase 1: Fact Collection (during body analysis)

For every local variable without an explicit annotation, record:

```
VarFacts:
    name: str
    writes: list[WriteInfo]        # every assignment to this var
    param_passes: list[ParamInfo]  # every call site where var is an argument
    returns: list[ReturnInfo]      # every return statement returning this var
    aliases: list[AliasInfo]       # variables assigned from this var
    method_calls: list[MethodCallInfo]  # methods called on this var (e.g. .append)
```

Each WriteInfo captures:

```
WriteInfo:
    type: TpyType          # type of the RHS
    expr: TpyExpr          # the expression (for diagnostics)
    line: int
    is_init: bool           # first assignment?
    source_var: str | None  # if RHS is another variable, its name (for alias tracking)
```

ParamInfo captures:

```
ParamInfo:
    param_type: TpyType    # the declared parameter type at the call site
    line: int
```

ReturnInfo captures:

```
ReturnInfo:
    return_type: TpyType   # the function's declared return type
    line: int
```

This replaces the current separate tracking structures (ListLiteralInfo,
StrVarInfo, write_history, literal_default_vars, etc.).

### Phase 2: Resolution (after body analysis)

For each variable with collected facts, resolve in dependency order
(aliases resolved after their sources):

#### Step 1: Determine candidate type from writes

Apply these rules in order:

**a) All writes are the same concrete type** -> use that type.

**b) Numeric widening** -- all writes are numeric types (int literals,
FixedInt, BigInt, Float):
- Compute the widest type using the numeric lattice.
- Int literals adapt to the widest concrete type.
- If any write is Float and others are integer types -> Float.
- Mixed signed/unsigned of same width -> error (force annotation).
- Bool mixed with numeric -> error (force annotation).

**c) None + single concrete type T** -- one or more writes are None, all
others are the same type T:
- Deduce `Optional[T]`.
- Only when None is the *initial* assignment (`x = None` as first write).
- `T` then `None` is an error -- if the programmer wants Optional,
  they should annotate. This prevents accidental None assignments from
  silently widening the type.

**d) Int literal range refinement** -- all writes are int literals or a
single FixedInt type:
- Check if all literal values fit the FixedInt range.
- If not, promote to BigInt.
- (Existing behavior, moved into unified pass.)

**e) Incompatible types** -> error.

#### Step 2: Refine from usage context

After determining the candidate type from writes, refine based on how
the variable is used:

**Parameter passing:** If the variable is passed to a typed parameter
and the candidate type is pending/ambiguous:
- List literal passed to `list[T]` param -> `list[T]`
- List literal passed to `Span[T]` param only -> stays Array
  (Span accepts both, Array is cheaper)
- List literal passed to both `list[T]` and `Span[U]` -> `list[T]`
- String passed to `String` param -> `str` (owned)
- Int literal passed to `Int64` param -> candidate narrows to `Int64`

**Return type:** If the variable is returned and the function has a
declared return type, use it to inform deduction:
- `return xs` where return type is `list[T]` -> xs is `list[T]`

**Conflicting usage:** If param passes / returns demand conflicting
types, error. In the face of ambiguity, refuse the temptation to guess --
the user must add an explicit annotation.

#### Step 3: Container-specific rules

For list literals (currently PendingListType):

| Condition | Resolved Type |
|-----------|---------------|
| Explicit annotation | use annotation |
| `.append()`, `.pop()`, etc. called | `list[T]` |
| Passed to `list[T]` param | `list[T]` |
| Reassigned with different-size literal | `list[T]` |
| Is global | `list[T]` |
| Default | `Array[T, N]` |

Element type follows from the write/usage analysis (widened if needed).

#### Step 4: String-specific rules

For string locals (currently PendingStrType):

| Condition | Resolved Type |
|-----------|---------------|
| Initialized from owned source (`str()`, function returning str) | `str` |
| `+=` used | `str` |
| Passed to `String` param | `str` |
| Reassigned from owned source | `str` |
| Default | `StrView` |

#### Step 5: Alias propagation

After all variables are resolved individually, propagate through alias
chains. This is a correctness requirement, not just a style preference:

**Strings:** `b = a` where `a` is a `StrView` generates
`std::string_view b = a`. If `a` is later promoted to `str` (e.g. via
`+=`), `b` would dangle. So `b` must also become `str`.

**Lists:** `b = a` where `a` is a `list` generates
`std::vector<T>& b = a` (a reference). This preserves Python's
shared-mutation semantics -- `b.append(x)` also mutates `a`. If `a`
stayed `Array`, the assignment would copy and mutations would diverge
from Python behavior. So if either variable requires `list`, both must
be `list`.

**Rules:**
- If `b = a` and either `a` or `b` is promoted to an owned/heap type,
  the other must also be promoted.
- If `b = a` then `b = [1,2,3]` (reassigned), the alias is broken --
  `b` is resolved independently.
- Iterate until stable (handles chains: `c = b`, `b = a`, `a` promoted).

### Phase 3: Apply resolved types

Update scope bindings, expr_types cache, and var_types for all resolved
variables. This is the same as today but done in one sweep instead of
three separate passes.

## Scenarios

### Works today (no change needed)

| Scenario | Deduced |
|----------|---------|
| `x = 0; x = 1_000_000_000_000` | BigInt |
| `x = 0; x = some_int32` | Int32 |
| `x = None; x = User()` | Optional[User] |
| `x = "hello"; print(x)` | StrView |
| `x = "hello"; x += " world"` | str |
| `x = [1,2,3]; for i in x: ...` | Array[Int32, 3] |
| `x = [1,2,3]; x.append(4)` | list[Int32] |

### New (enabled by unified pass)

| Scenario | Deduced |
|----------|---------|
| `x = 0; x = 3.14` | float |
| `x = get_int32(); x = get_int64()` | Int64 |
| `x = 3.14; x = 42` | float |
| `x = [1,2,3]; x = [4,5]` (different sizes) | list[Int32] |
| `x = [1,2]; x.append(big_int64)` | list[Int64] |
| `x = [1,2,3]; return x` (return type `list[T]`) | list[T] |
| `a = [1,2,3]; b = a; b.append(4)` -> a also list | list[Int32] |

### Errors (by design)

| Scenario | Why |
|----------|-----|
| `x = True; x = 42` | bool is not numeric for deduction |
| `x = get_int32(); x = get_uint32()` | mixed sign, same width |
| `x = User(); x = None` | T then None requires annotation |
| `x = 42; x = "hello"` | incompatible types |
| `x = User(); x = Config()` | unrelated classes |
| `x = None` (never concrete) | cannot deduce Optional[???] |

## Implementation Plan

1. Define `VarFacts`, `WriteInfo`, `ParamInfo`, `ReturnInfo`, `AliasInfo`
   data structures.

2. Add fact collection hooks in `statements.py` and `expressions.py` --
   these replace the current `mark_*` calls and `record_write` calls.

3. Implement `LocalTypeDeduction.resolve()` containing the resolution
   logic (steps 1-5 above). This replaces `ListLiteralTracker`,
   `StrVarTracker`, and the resolution parts of `ReassignmentInference`.

4. Call `resolve()` from `analyzer.py` at the existing three call sites
   (after function body, after method body, after top-level code),
   replacing the current three separate calls.

5. Keep `ReassignmentInference.retro_validate_against_annotation()` --
   this validates writes against explicit annotations and is orthogonal
   to deduction. It stays inline.

6. Remove `ListLiteralTracker`, `StrVarTracker`, and the deduction parts
   of `ReassignmentInference`. Clean up `SemanticContext` tracking dicts.

7. Update tests, add new test cases for the new scenarios.

## Resolved Questions

- **Alias tracking for lists:** Yes, propagate. `b = a` on a list
  generates a reference (`std::vector<T>& b = a`) to preserve Python's
  shared-mutation semantics. If `b` is mutated (e.g. `b.append(x)`),
  `a` must also be `list` -- otherwise `b = a` would copy an Array and
  mutations wouldn't be shared. Same correctness motivation as strings.

- **Multiple param passes with different element types:** Error. In the
  face of ambiguity, refuse the temptation to guess. If `xs` is passed
  to both `f(list[Int32])` and `g(list[Int64])`, the user must annotate.

- **Interaction with narrowing:** Deduced `Optional[T]` should work
  with narrowing (`if x is not None`). Deferred to Phase 3 if the
  plumbing is non-trivial.

## Post-1.0: Per-Assignment-Segment Typing

The current model assigns a single type per variable name. This can be
suboptimal when a variable's usage falls into distinct segments separated
by reassignment:

```python
a = "x"         # segment 1: only needs StrView
b = a            # could be string_view here
a += " y"        # segment 2: needs str
# b is never read after a mutates -- the copy was unnecessary
```

```python
b = a            # segment 1: could be string_view (cheap)
b = str(99)      # segment 2: needs str
# b's first segment didn't need an allocation
```

With SSA-style reasoning (each assignment creates a new "version" with
its own type), the compiler could use the narrowest type per segment
and only widen at the reassignment boundary. This would avoid unnecessary
allocations/copies in the first segment.

This applies to strings (StrView vs str) and potentially lists (Array vs
list) in the same way. It requires liveness/escape analysis to prove
that the narrow-typed segment doesn't escape into the wider one.

Not in scope for pre-1.0 -- the current one-type-per-variable model is
correct, just conservative.
