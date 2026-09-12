# Bidirectional Type Inference

## Vision

TurboPython should infer types from context the way Rust does: when the expected
type is known, the compiler uses it to resolve generic parameters without forcing
the programmer to spell them out. This applies uniformly to functions,
constructors, and methods -- the same inference mechanism everywhere.

Rust's type inference is based on Hindley-Milner (HM) unification, where type
variables flow bidirectionally through constraints. We take a pragmatic,
incremental approach: start with a targeted return-type fallback (Phase 1-2),
evaluate whether to adopt full HM-style constraint solving later (Phase 3+).

```python
def make_empty[T]() -> list[T]: ...

x: list[int32] = make_empty()       # T = int32 from assignment context
return make_empty()                  # T from function return type
c = Container()                      # type params from c's known type
g(make_empty())                      # T from g's parameter type
unsafe_cast[uint32](p)               # T explicit, U from arg (partial type args)
```

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Return-type inference fallback for assignment + return context (exact/generic matches only) | **Done** |
| **Phase 2** | Nested call context + partial explicit type args + unsafe_cast cleanup | **Done** |
| **Phase 3a.1** | Forward-propagation from usage (MVP): `x = GenericType()` with unresolved type params, eagerly resolved from subsequent method calls (e.g. `x.push(int32(0))` constrains T=int32). Lifecycle managed by `LocalTypeDeduction`; unification reuses `match_type_with_inference`. See `LOCAL_TYPE_DEDUCTION.md` Phase 7a.1. | **Done** |
| **Phase 3a.2** | Expected-type constraint sources: parameter passing (`f(x)` where `f` expects `Container[int32]`) and return-type context (`return x` where function returns `Container[int32]`). See `LOCAL_TYPE_DEDUCTION.md` Phase 7a.2. | **Done** |
| **Phase 3b** | `_` wildcard for partial type args in all generic calls (functions, constructors, methods). See `LOCAL_TYPE_DEDUCTION.md` Phase 7b. | **Done** |
| ~~Phase 3c~~ | ~~Field access and cascading pending types.~~ Moved to Future Extensions in `LOCAL_TYPE_DEDUCTION.md`. | -- |

## Future Extensions (no known use case yet)

| Extension | Description |
|-----------|-------------|
| Coercion-aware return-type matching | Allow numeric widening when matching return type against expected type. Type param inference always produces exact matches today. |
| Overload filtering by return type | When multiple overloads match arguments, use expected return type as a filter. Overloads are distinguished by argument types today. |
| Joint inference: pending arg co-resolved by a sibling arg | A pending-typed local (`heap = []`) passed to a generic call alongside an arg that fixes the type param (`heappush(heap, Entry(...))`) should resolve the local from the co-inferred param. The directional matcher resolves one arg at a time with no shared unification variable, so this is deferred to HM-style constraint solving. Has a real use case (generic index-heap); IR-gated -- see `IR_DESIGN.md` "What THIR/MIR unblocks". |
| Generic function result resolved through an intermediate local | `def f() -> list[int32]: x = make_empty(); return x` (where `make_empty[T]() -> list[T]`) fails to infer `T` -- the generic *call*'s type param is only fixed by the `return x` use site, transitively through `x`. Phase 3a.2 resolves a *direct* `return make_empty()` and 3a.1 resolves a pending local from later *method* calls, but not a generic-call result bound to a local then constrained by a use site. Empty container literals (list/dict/set) and user generic records already resolve this way; only generic-function results via a local don't. The general form is the same HM constraint-solving step as the sibling-arg row above (back-propagate the use-site type through the local to the call's free type var). Workaround: annotate the local, or `make_empty[int32]()`. Deferred to Phase-3+/IR. Surfaced extending empty-container return-context resolution. |

## Design Principles

1. **Context refines, never obscures**: expected type only fills in unresolved
   type params after argument-based inference. It never overrides what args
   already determined.

2. **Ambiguity stays explicit**: if multiple candidates remain viable, require
   explicit type arguments or annotations -- no silent tie-breaking.

3. **Existing behavior is stable**: calls that already resolve unambiguously from
   arguments keep resolving the same way. Expected type is a fallback, not a
   primary signal.

4. **Uniform mechanism**: functions, constructors, and methods use the same
   inference path. No special-casing by call kind.

---

## Phase 1: Return-Type Inference Fallback

### How It Works

Given a generic call where argument-based inference leaves type params unresolved,
and an expected type is available from context:

1. Infer type params from arguments (existing behavior, unchanged)
2. If unresolved params remain, match declared return type against expected type
3. If all params now resolved, proceed; otherwise fail with clear diagnostic

The "match return type against expected type" step reuses the existing
`match_type_with_inference` -- the same function that matches `list[T]` against
`list[int32]` for arguments.

### Context Sources (Phase 1)

| Source | Example | Mechanism |
|--------|---------|-----------|
| Variable declaration | `x: list[int32] = make_empty()` | `analyze_expr_with_hint(init, annotation)` -- already exists |
| Reassignment | `x = make_empty()` where x is `list[int32]` | Thread target type as hint |
| Return statement | `return make_empty()` in fn returning `list[int32]` | Thread return annotation as hint |

### What It Enables

```python
# Generic functions with no args that constrain T
def make_empty[T]() -> list[T]: ...
x: list[int32] = make_empty()           # infers T = int32

# Generic functions with partial arg coverage
def wrap[T](n: int32) -> list[T]: ...
x: list[int32] = wrap(5)                # args give int32 for n, context gives T

# Return context
def get_items() -> list[int32]:
    return make_empty()                  # infers T = int32 from return type

# Reassignment context
x: list[int32] = [1, 2, 3]
x = make_empty()                         # infers T = int32 from x's type

# Record constructors (same mechanism, not special-cased)
c: Container[int32] = Container()        # infers T = int32
p: Ptr[int32] = Ptr()                    # infers T = int32
```

### Conflict Rule

If arguments fully determine all type params, expected type is not consulted.
Any mismatch between the inferred return type and the expected type is a normal
type error at the assignment/return level -- same as today.

```python
def identity[T](x: T) -> T: ...
x: Float = identity(int32(1))           # T = int32 from args; Float vs int32
                                         # is a type mismatch, not an inference issue
```

---

## Phase 2: Nested Call Context + Partial Type Args

### Nested Call Inference

Parameter types from outer calls flow as hints to inner calls:

```python
def sink(items: list[int32]) -> None: ...
sink(make_empty())                       # expected type for make_empty() = list[int32]
```

This works because `_analyze_user_function_call` already calls
`analyze_expr_with_hint(arg, ptype)`, which sets `ctx.expr_type_hint` before
analyzing the argument. Phase 1's return-type fallback then picks it up.

Record constructor arguments also propagate hints (both generic and non-generic
records use `analyze_expr_with_hint` for constructor args).

### Partial Explicit Type Args

Generic functions accept fewer explicit type args than type params. The remaining
params are inferred from arguments and/or context:

```python
def transform[T, U](x: U) -> T: ...
# unsafe_cast[uint32](p) -- T=uint32 explicit, U=int32 from arg
```

`infer_type_params_for_function` accepts an `explicit_type_args` parameter that
pre-populates the inferred dict. `match_type_with_inference` handles consistency
checks for pre-populated entries. This works for both user generic functions and
builtin generic overloads.

### unsafe_cast Cleanup

`unsafe_cast` was previously a special-cased builtin with custom sema logic.
It is now defined as a standard two-type-param generic function:

```python
# T = target pointee type, U = source pointee type
unsafe_cast[T, U]:
    Ptr[U] -> Ptr[T]        # reinterpret_cast<T*>(p)
    Ptr[readonly[U]] -> Ptr[readonly[T]]  # reinterpret_cast<const T*>(p)
```

Usage patterns:
- `unsafe_cast[uint32](p)` -- T=uint32 explicit, U inferred from arg
- `q: Ptr[uint32] = unsafe_cast(p)` -- U from arg, T from context
- Ptr[readonly[T]] arg with Ptr context correctly fails (readonly overload returns
  Ptr[readonly[T]], which doesn't match Ptr target)

