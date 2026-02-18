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

x: list[Int32] = make_empty()       # T = Int32 from assignment context
return make_empty()                  # T from function return type
c = Container()                      # type params from c's known type
g(make_empty())                      # T from g's parameter type (future)
```

Today, inference is argument-directed only: type params must be determinable from
the arguments. Expected-type context is used only by `unsafe_cast` (ad hoc
special case) and builtin generic constructors (`list()`, `Ptr()`). This design
generalizes that into a single mechanism.

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Return-type inference fallback for assignment + return context (exact/generic matches only) | **In progress** |
| **Phase 1b** | Coercion-aware return-type matching (numeric widening etc.); (optional, may be postponed) | Planned |
| **Phase 2** | Nested call context: `g(f(...))` where g's param type constrains f. Evaluate whether to adopt HM-style constraint solving or continue incremental. | Planned |
| **Phase 3** | Overload filtering by expected return type | Planned |
| **Phase 4** | Remove `unsafe_cast` special-case, unify with general mechanism | Planned |

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
`list[Int32]` for arguments.

### Context Sources (Phase 1)

| Source | Example | Mechanism |
|--------|---------|-----------|
| Variable declaration | `x: list[Int32] = make_empty()` | `analyze_expr_with_hint(init, annotation)` -- already exists |
| Reassignment | `x = make_empty()` where x is `list[Int32]` | Thread target type as hint |
| Return statement | `return make_empty()` in fn returning `list[Int32]` | Thread return annotation as hint |

### What It Enables

```python
# Generic functions with no args that constrain T
def make_empty[T]() -> list[T]: ...
x: list[Int32] = make_empty()           # infers T = Int32

# Generic functions with partial arg coverage
def wrap[T](n: Int32) -> list[T]: ...
x: list[Int32] = wrap(5)                # args give Int32 for n, context gives T

# Return context
def get_items() -> list[Int32]:
    return make_empty()                  # infers T = Int32 from return type

# Reassignment context
x: list[Int32] = [1, 2, 3]
x = make_empty()                         # infers T = Int32 from x's type

# Record constructors (same mechanism, not special-cased)
c: Container[Int32] = Container()        # infers T = Int32
p: Ptr[Int32] = Ptr()                    # infers T = Int32
```

### Conflict Rule

If arguments fully determine all type params, expected type is not consulted.
Any mismatch between the inferred return type and the expected type is a normal
type error at the assignment/return level -- same as today.

```python
def identity[T](x: T) -> T: ...
x: Float = identity(Int32(1))           # T = Int32 from args; Float vs Int32
                                         # is a type mismatch, not an inference issue
```

---

## Phase 2: Nested Call Context (Planned)

Propagate expected type into nested call arguments:

```python
def sink(items: list[Int32]) -> None: ...
sink(make_empty())                       # expected type for make_empty() = list[Int32]
```

This requires resolving the outer call's parameter types before analyzing inner
arguments. The current architecture analyzes arguments first, so this needs
careful ordering changes.

This is the natural evaluation point for whether to adopt Hindley-Milner style
constraint solving. HM uses type variables and unification to propagate
constraints bidirectionally -- rather than patching individual call sites, all
constraints are collected and solved together. If Phase 2 plumbing gets complex,
that's the signal to invest in a proper constraint engine.

## Phase 3: Overload Filtering by Return Type (Planned)

When multiple overloads match arguments, use expected return type as a filter:

1. Build candidate set from name/arity
2. Argument-based viability check (existing)
3. If expected type exists, filter by return type compatibility
4. If one remains, resolve; if zero, error with context mention; if multiple,
   require explicit disambiguation

Expected type is a filter, not a scoring bonus. No heuristic tie-breakers.

## Phase 4: Unify unsafe_cast (Planned)

`unsafe_cast` currently has ad hoc special handling to read `ctx.expr_type_hint`
for context-driven resolution (`q: Ptr[U] = unsafe_cast(p)`). Once the general
mechanism is mature, `unsafe_cast` can use the standard path and the special case
can be removed.

---

