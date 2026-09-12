# Local Variable Type Deduction -- Unified Post-Body Pass

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Unify infrastructure: replace ListLiteralTracker, StrVarTracker, and deduction parts of ReassignmentInference with single LocalTypeDeduction class. Preserve existing behavior. | Done |
| 2a | Numeric widening across assignments (int->float, int32->int64, unsigned->wider signed). | Done |
| 2b | Different-size list reassignment, return-type-driven deduction, alias propagation for lists. | Done |
| 2c | Cross-variable list reassignment (`a = [1,2,3]; b = [4,5]; a = b` -- both should become list). | Done |
| 3 | Narrowing integration: deduced `Optional[T]` variables work with `if x is not None` narrowing. | Done |
| 4 | String deduction test coverage: dedicated tests for StrView-vs-str resolution and string alias propagation. | Done |
| 5a | Empty list inference: `xs = []; xs.append(v)` and `xs = list(); xs.append(v)` infer element type from `.append()`/`.insert()` usage, with numeric widening and alias propagation. | Done |
| 5b | Empty dict inference: `d = {}; d[k] = v` and `d = dict()` infer key/value types from subsequent subscript assignment. | Done |
| 5c | Empty set inference: `s = set(); s.add(v)` infers element type from subsequent `.add()` calls. | Done |
| 5d | Unify empty container inference: extract shared helpers for list/dict/set (PENDING_CONTAINER_TYPES constant, unified container lookup, shared resolution epilogue, merged param context tracking). Single code paths prevent forgetting one container type. | Done |
| 6 | List element-type widening: `.append(int64)` on `[1,2]` widens element type from int32 to int64. Already handled by `_widen_inferred_type` in Phase 5a infrastructure; added test coverage. | Done |
| 7a.1 | Deferred generic instance inference (MVP): `x = GenericType()` with unresolved type params, resolved from subsequent method calls via constraint unification. Eager resolution once all params known. Reuses `match_type_with_inference` from bidirectional inference. See `BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md`. | Done |
| 7a.2 | Expected-type constraint sources: resolve pending generic from function parameter type (`f(x)` where param is `Container[int32]`) and return type (`return x` where function returns `Container[int32]`). Same eager resolution as 7a.1. | Done |
| 7b | `_` wildcard for partial type args in all generic calls (functions, constructors, methods). `ArrayList[_, 1024]()`, `f[_, int32](x)`, etc. Remaining constructor params deferred via 7a. | Done |

## Future Extensions (post-1.0)

| Extension | Description |
|-----------|-------------|
| Per-assignment-segment typing | SSA-style reasoning: each assignment to a variable creates a new "version" with its own type. Enables narrower types per segment (e.g. StrView before reassignment, str after), avoiding unnecessary allocations. Requires liveness/escape analysis. See details at end of document. |
| Empty list to Array promotion | `xs = []; xs.append(1); xs.append(2)` could resolve to `Array[int32, 2]` if the final size is statically known (no dynamic mutations like loop appends or pop/remove). Would need to compute max required size from constant append/insert/extend counts. Likely low priority -- in hot paths users would declare `Array` explicitly with a known max size. |
| Union expected-type resolution | `f(x)` where param is `Container[int32] | str` -- try each union member as a candidate for resolving pending generic instances. Currently only plain `NominalType` and `Optional[NominalType]` are tried. |
| Extended constraint sources | Field access as constraint (`v: int32 = c.val` resolves T), cascading pending types (`x = s.items` where both pending). Niche -- existing sources (method calls, parameter passing, return types) cover practical cases. |

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
- Int literal passed to `int64` param -> candidate narrows to `int64`

**Return type:** If the variable is returned and the function has a
declared return type, use it to inform deduction (list / dict / set alike):
- `return xs` where return type is `list[T]` -> xs is `list[T]`
- `return d` where return type is `dict[K, V]` -> d is `dict[K, V]`
- `return s` where return type is `set[T]` -> s is `set[T]`

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
| `x = 0; x = some_int32` | int32 |
| `x = None; x = User()` | Optional[User] |
| `x = "hello"; print(x)` | StrView |
| `x = "hello"; x += " world"` | str |
| `x = [1,2,3]; for i in x: ...` | Array[int32, 3] |
| `x = [1,2,3]; x.append(4)` | list[int32] |

### New (enabled by unified pass)

| Scenario | Deduced |
|----------|---------|
| `x = 0; x = 3.14` | float |
| `x = get_int32(); x = get_int64()` | int64 |
| `x = 3.14; x = 42` | float |
| `x = [1,2,3]; x = [4,5]` (different sizes) | list[int32] |
| `x = [1,2]; x.append(big_int64)` | list[int64] |
| `x = [1,2,3]; return x` (return type `list[T]`) | list[T] |
| `d = {}; return d` (return type `dict[K, V]`) | dict[K, V] |
| `s = set(); return s` (return type `set[T]`) | set[T] |
| `a = [1,2,3]; b = a; b.append(4)` -> a also list | list[int32] |

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
  to both `f(list[int32])` and `g(list[int64])`, the user must annotate.

- **Interaction with narrowing:** Deduced `Optional[T]` should work
  with narrowing (`if x is not None`). Deferred to Phase 3 if the
  plumbing is non-trivial.

## Phase 7: Deferred Generic Instance Inference

### Overview

When a generic type is constructed without explicit type arguments and without
enough context to infer type params (no annotation, no constructor args that
constrain T), the compiler defers inference and resolves type params from
subsequent method calls on the variable.

```python
class Stack[T]:
    def __init__(self):
        self.items: list[T] = []
    def push(self, item: T) -> None:
        self.items.append(item)
    def pop(self) -> T:
        return self.items.pop()

s = Stack()        # T unknown -- defer
s.push(42)         # match T against int32 -> T = int32, all resolved -> eager resolve
x = s.pop()        # s is now Stack[int32], normal resolution -> x: int32
```

This applies to all generic records (user-defined and library types like
`Box[T]`), not just built-in containers.

### Phase 7a: MVP -- Eager Resolution from Method Calls

#### Representation

New type in `typesys.py`:

```
PendingGenericInstanceType:
    record_name: str
    instance_id: int
```

New tracking info (in context or typesys):

```
PendingGenericInstanceInfo:
    instance_id: int
    variable_name: str
    record_info: RecordInfo
    type_params: list[str]          # ["T"]
    inferred: dict[str, TpyType]    # grows: {} -> {"T": int32}
    expr: TpyCall                   # for diagnostics
    decl_line: int | None
```

New context state in `SemanticContext`:

```
pending_generic_instances: dict[int, PendingGenericInstanceInfo]
variable_to_generic_instance: dict[str, int]   # var_name -> instance_id
```

#### Creation: `_analyze_record_constructor` in `calls.py`

When a generic record constructor has no explicit type args, no args that
constrain type params, and no contextual hint -- instead of erroring, check
if the constructor can be called with the given args (possibly zero). If yes,
create a `PendingGenericInstanceInfo`, register in context, return
`PendingGenericInstanceType`.

The variable gets the pending type in scope, which gates what operations
are allowed until resolution.

#### Constraint accumulation: method calls in `methods.py`

In `analyze_method_call`, when the receiver type is `PendingGenericInstanceType`:

1. Look up the method on `record_info.methods` (raw, unsubstituted signatures)
2. Analyze all arguments to get concrete `arg_types`
3. For each `(param_type, arg_type)` pair, call `match_type_with_inference`
   to accumulate constraints into `info.inferred`
4. **IntLiteralType resolution**: before binding, resolve `IntLiteralType` via
   `default_int_for_literal` -- literal types are meaningless as type args
5. Check for conflicts (same param bound to incompatible types -> error)
6. After accumulating, check if all type params are resolved:
   - **All resolved -> eager resolution** (see below)
   - **Void-returning method with unresolved params** -> OK, continue
   - **Method returning an unresolved TypeParamRef** -> error: "Cannot
     determine return type of '{method}'; add type annotation to constrain
     type parameter {T}"

#### Eager resolution

When all type params become known after a constraining method call:

1. Build `NominalType(record_name, tuple(inferred[tp] for tp in type_params))`
2. Validate type param bounds (protocol conformance)
3. Update scope binding, `expr_types`, `declared_var_types`, `var_types`
4. Remove from `pending_generic_instances`
5. Continue analyzing the current method call with the now-concrete receiver
   (substituted method signature, proper return type)

After eager resolution, all subsequent usage of the variable is normal --
no special handling needed.

#### Safety net in `resolve_all()`

After existing container/string resolutions, check `pending_generic_instances`
for any unresolved entries. Error: "Cannot infer type arguments for '{name}';
add explicit type args (e.g., {name}[T]()) or use the variable so types can
be inferred".

#### Restrictions during pending state

| Operation | Allowed? | Why |
|-----------|----------|-----|
| Method call with constraining args, void return | Yes | Primary inference path |
| Method call, no type-param-dependent args, void return | Yes | No-op for inference (e.g. `s.clear()`) |
| Method call returning unresolved TypeParamRef | Error | Can't determine expression type |
| Field access | Error | Field type depends on unresolved params |
| Pass to function with matching param type | Yes | Resolves from expected type (7a.2) |
| Pass to function with unrelated param type | Error | Can't extract type constraints |
| Return where function return type matches | Yes | Resolves from expected type (7a.2) |
| Reassignment | Error | Semantics unclear for pending type |
| Comparison / operators | Error | Requires concrete type |

#### Scenarios

| Code | Result |
|------|--------|
| `s = Stack(); s.push(42)` | `s: Stack[int32]` (eager after push) |
| `s = Stack(); s.push(42); x = s.pop()` | `s: Stack[int32]`, `x: int32` |
| `s = Stack(); s.push(42); s.push(int64(0))` | Error: conflicting constraints for T (int32 vs int64) |
| `s = Stack(); x = s.pop()` | Error: cannot determine return type, T unresolved |
| `s = Stack(); consume_stack(s)` where `consume_stack(s: Stack[int32])` | `s: Stack[int32]` (resolved from param type) |
| `s = Stack(); return s` where return type is `Stack[int32]` | `s: Stack[int32]` (resolved from return type) |
| `s = Stack(); print(s)` | Error: pending type cannot be passed as parameter |
| `s = Stack()` (no constraining calls) | Error in resolve_all(): cannot infer T |

### Phase 7a.2: Expected-Type Constraint Sources

Resolve pending generic instances from known expected types at function
parameter and return sites. Same eager resolution mechanism as 7a.1,
different integration points.

```python
def consume(c: Container[int32]) -> None: ...
def make() -> Container[int32]:
    c = Container()
    return c        # return type constrains T = int32

c = Container()
consume(c)          # parameter type constrains T = int32
```

Both use `match_type_with_inference(record_pattern, expected_type, inferred)`
to extract type params from the expected type.

#### Integration point: `check_type_compatible` in `compatibility.py`

Single integration point for both param passing and return types. When
`check_type_compatible` encounters `PendingGenericInstanceType` as the
actual type, it calls `methods.try_resolve_pending_from_expected_type()`
before erroring. This method:

1. Unwraps `Own`/`Optional`/`readonly` from the expected type
2. Checks if the inner type is a matching `NominalType` for the same record
3. Builds a pattern type with `TypeParamRef`s for unresolved params
4. Calls `match_type_with_inference` to extract constraints
5. If all params resolved, calls `_eagerly_resolve_pending_generic`
6. Updates the source expression's type in the expr cache

After resolution, `check_type_compatible` recurses with the concrete type,
which handles any remaining compatibility logic (e.g., `T -> Optional[T]`
wrapping for Optional return types).

The `methods` reference is wired to `TypeCompatibility` via `set_methods()`
after construction (same pattern as `type_ops`/`protocols`).

**Limitation**: Union expected types (e.g., `Container[int32] | str`) are not
tried as constraint sources. `T | None` works (normalized to `Optional[T]`),
but multi-member unions fall through to the "unresolved" error.

### Phase 7b: `_` Wildcard for Partial Type Args

Syntax: `_` as a wildcard placeholder in any type arg position, for all
generic calls (functions, constructors, methods). `f[_]()` is equivalent
to `f()` (full inference).

```python
# Functions -- _ in any position
pair_func[_, int64](int32(5), int64(20))  # T inferred from arg, U = int64
triple[int32, _, int64](a, b, c)          # B inferred from arg

# Constructors -- remaining params deferred (reuses 7a)
b = Box[_](int32(42))                     # T = int32 from arg
p = Pair[_, int64](int32(10), int64(20))  # T = int32, U = int64

# Methods -- wildcards on method-level type params
m.transform[_, int64](x, int64(100))      # U inferred, V = int64
```

Implementation:
- Parser (`_parse_type_args_from_subscript`): detects `_` (ast.Name with
  id `_`) and emits `None` in the type_args tuple
- `infer_type_params_for_function` and `infer_type_params_for_record`
  accept `explicit_type_args` and pre-populate the `inferred` dict;
  `None` entries are skipped (wildcard positions)
- Function calls (`calls.py`): wildcards route through inference with
  `explicit_type_args` instead of the full-explicit path
- Constructor calls (`calls.py`): wildcards validated for count, then
  passed to `infer_type_params_for_record`; remaining unresolved params
  enter the Phase 7a deferred resolution path
- Method calls (`methods.py`): wildcards route to inference with
  `explicit_type_args` on the partial function
- `_validate_explicit_type_args` skips `None` entries in protocol/type
  validation

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
