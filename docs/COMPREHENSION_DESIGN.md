# Comprehensions

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | List comprehension: `[expr for var in iterable]`, single generator, no filter | Done |
| 2 | Filter clause: `[expr for var in iterable if cond]` | Done |
| 3 | Tuple unpacking in generator: `[v for k, v in pairs]` | Done |
| 4 | Annotation propagation: `result: list[Int32] = [x for x in items]` | Done |
| 5 | Optimizations: Sized iterables -> `reserve()`, range -> `reserve()` | Done |

### Future Extensions

| Feature | Notes |
|---------|-------|
| `range(N)` -> Array | `[i*i for i in range(5)]` -> `std::array<T, 5>` (zero heap alloc). Requires literal N, no filter, value-type elements |
| Dict comprehension | `{k: v for k, v in items}` -- same generator model, produces `dict[K, V]`. See [Dict comprehension](#dict-comprehension-future) section |
| Set comprehension | `{x for x in items}` -- requires `set` type (D9) |
| Generator expressions | `sum(x*x for x in items)` -- lazy evaluation, no allocation. See [Generator expressions](#generator-expressions-future) section |
| Nested generators | `[f(x, y) for x in a for y in b]` -- multiple `comprehension` nodes. Low priority (rare in practice) |
| Walrus operator in filter | `[y for x in items if (y := f(x)) > 0]` -- requires walrus operator |
| Async comprehensions | `[x async for x in aiter]` -- requires async/await (G1) |

### Known Limitations (once implemented)

| Area | Detail |
|------|--------|
| Single generator only (Phase 1-5) | Nested generators (`for x in a for y in b`) are deferred |
| No starred unpacking | `[*a, *b]` inside comprehensions is not supported |

---

## Overview

List comprehensions are one of Python's most distinctive features -- concise, readable
expressions that build lists from iterables with optional filtering.

```python
from tpy import Int32

squares = [x * x for x in range(10)]
evens = [x for x in items if x % 2 == 0]
names = [p.name for p in people]
```

They are used in 16+ files of the TPy compiler source and are a prerequisite for
self-hosting. More importantly, they're idiomatic Python that users expect to work.

The key insight is that all Python comprehension forms (`ListComp`, `DictComp`,
`SetComp`, `GeneratorExp`) share the same `comprehension` generator structure in the
AST. Designing the generator infrastructure well means dict/set comprehensions and
generator expressions come cheaply later.

---

## Design Principles

1. **Expression semantics via IIFE**: Comprehensions are expressions, not statements.
   In C++, the cleanest way to produce a value from a loop is an immediately-invoked
   lambda (IIFE). This slots naturally into any expression context -- assignments,
   function arguments, return values.

2. **Reuse existing infrastructure**: The for-loop analysis (`get_iterable_element_type`,
   scope management, iteration codegen) and list type system (`ListType`,
   `PendingListType`) already exist. Comprehensions build on top of these rather than
   introducing parallel paths.

3. **Shared generator model**: The new `TpyComprehensionGenerator` node is reusable
   across list/dict/set comprehensions and generator expressions. Design it once.

4. **Optimize where obvious**: `range(N)` with literal N can produce `Array[T, N]`
   (zero heap allocation). Sized iterables can `reserve()`. But the baseline must work
   first -- optimizations are a later phase.

5. **CPython compatible**: List comprehensions are native Python. No stubs or
   `no_cpython.txt` needed.

---

## Python AST Structure

All comprehension forms use the same `comprehension` generator node:

```python
# Python AST for: [x * 2 for x in items if x > 0]
ListComp(
    elt=BinOp(left=Name('x'), op=Mult(), right=Constant(2)),
    generators=[
        comprehension(
            target=Name('x', Store()),       # loop variable
            iter=Name('items', Load()),      # iterable
            ifs=[Compare(...)],              # filter conditions (0 or more)
            is_async=0
        )
    ]
)
```

`DictComp` has `key` + `value` instead of `elt`. `GeneratorExp` is identical to
`ListComp` structurally. All share the `generators` list of `comprehension` nodes.

Phase 1-5 restricts to `len(generators) == 1`. Nested generators are a future extension.

---

## AST Nodes

### TpyComprehensionGenerator

Shared generator node, reusable across comprehension kinds:

```python
@dataclass
class TpyComprehensionGenerator:
    """Single generator clause: `for var in iterable [if cond]*`"""
    var: str                           # loop variable name
    iterable: TpyExpr                  # iterable expression
    conditions: list[TpyExpr]          # filter conditions (may be empty)
    unpack_vars: list[str] | None      # Phase 3: tuple unpacking (k, v)
```

`unpack_vars` is `None` for simple `for x in items` and a list of names for
`for k, v in items`. Only one of `var` / `unpack_vars` is meaningful at a time.
(Phase 3 adds unpacking; Phase 1 uses `var` only.)

### TpyListComprehension

```python
@dataclass
class TpyListComprehension(TpyExpr):
    """List comprehension: [expr for var in iterable if cond]"""
    element_expr: TpyExpr
    generator: TpyComprehensionGenerator
```

Single generator field (not a list) since we restrict to one generator.

---

## Parser

Handle `ast.ListComp` in `_parse_expr`:

```python
elif isinstance(node, ast.ListComp):
    if len(node.generators) != 1:
        raise ParseError("Nested comprehensions not yet supported", node)
    gen = node.generators[0]
    if gen.is_async:
        raise ParseError("Async comprehensions not yet supported", node)
    # Parse generator
    generator = self._parse_comprehension_generator(gen)
    element_expr = self._parse_expr(node.elt)
    return TpyListComprehension(element_expr, generator, loc=loc)
```

The `_parse_comprehension_generator` helper parses the shared `comprehension` node:

```python
def _parse_comprehension_generator(self, gen: ast.comprehension) -> TpyComprehensionGenerator:
    iterable = self._parse_expr(gen.iter)
    conditions = [self._parse_expr(c) for c in gen.ifs]

    if isinstance(gen.target, ast.Name):
        return TpyComprehensionGenerator(
            var=gen.target.id, iterable=iterable,
            conditions=conditions, unpack_vars=None)
    elif isinstance(gen.target, ast.Tuple):
        # Phase 3: tuple unpacking
        ...
    else:
        raise ParseError("Unsupported comprehension target", gen.target)
```

This helper is shared -- `DictComp` and `GeneratorExp` will call it too.

---

## Semantic Analysis

Comprehension analysis creates a temporary scope for the loop variable,
types it from the iterable's element type, then analyzes the element expression.

### Analysis flow

```python
def _analyze_list_comprehension(self, expr: TpyListComprehension) -> TpyType:
    gen = expr.generator

    # 1. Analyze iterable (outside comprehension scope)
    iterable_type = self.analyze_expr(gen.iterable)

    # 2. Extract element type from iterable
    elem_type = self.iterable.get_iterable_element_type(iterable_type, loc=expr.loc)

    # 3. Open scope, register loop variable
    # (similar to TpyForEach analysis in statements.py)
    with self.scopes.comprehension_scope():
        self.scopes.declare(gen.var, elem_type)

        # 4. Analyze filter conditions (Phase 2)
        # No type restriction -- matches if/while behavior (truthy dispatch in codegen)
        for cond in gen.conditions:
            self.analyze_expr(cond)

        # 5. Analyze element expression
        result_elem_type = self.analyze_expr(expr.element_expr)

    # 6. Return list type
    return ListType(result_elem_type)
```

### Scoping

Comprehensions have their own scope in Python 3 (unlike Python 2 where the loop
variable leaked). The loop variable is not visible after the comprehension. This maps
naturally to a C++ lambda scope (IIFE) -- variables declared inside the lambda don't
escape.

The `comprehension_scope()` context manager works like the existing `loop_scope()` but
without break/continue support.

### Type inference details

- **Element type**: Determined by analyzing `element_expr` with the loop variable in
  scope. Standard expression analysis, no special rules.
- **Filter conditions**: No type restriction -- conditions are passed to
  `gen_truthy_expr` in codegen, same as `if`/`while`. Filter does not affect
  element type.
- **Result type**: `ListType(result_elem_type)` -- always a `list[T]`. Unlike array
  literals, comprehensions don't use `PendingListType` in Phase 1 because the result
  size is generally unknown. Phase 5 adds optimization for known-size cases.

### Ownership

`[p for p in people]` where `people: list[Person]` produces a new `list[Person]`
where each element is copied into the result via `push_back`. This is consistent
with how a for-loop + `append` would behave -- the result list owns its elements.

For `@nocopy` types, the element expression must produce an owned value (e.g.
`[make_thing(x) for x in inputs]` where `make_thing` returns `Own[Thing]`).
Copying a `@nocopy` loop variable into the result is an error, same as
`items.append(nocopy_ref)` would be.

### Edge case: empty iterable

`[f(x) for x in empty_list]` where `empty_list: list[Int32]` works fine -- the
loop variable type is `Int32` (from the list's element type), the element expression
is analyzed, and the result is an empty `list[T]`. The iterable's element type is
always known from the iterable's type, regardless of runtime emptiness.

---

## Code Generation

### IIFE Pattern

Comprehensions generate an immediately-invoked lambda expression (IIFE):

```python
# [x * 2 for x in items]
```

```cpp
[&]() {
    std::vector<int32_t> __result;
    auto& __obj_1 = items;
    auto __beg_1 = __obj_1.begin();
    auto __end_1 = __obj_1.end();
    for (; __beg_1 != __end_1; ++__beg_1) {
        auto& x = *__beg_1;
        __result.push_back(x * 2);
    }
    return __result;
}()
```

The IIFE pattern:
- Works in any expression context (assignment, argument, return)
- Naturally scopes the loop variable
- Reuses the existing begin/end loop codegen shape from `_gen_begin_end_loop`
- The `[&]` capture is safe -- the lambda executes immediately, no lifetime issues
- Return type is deduced by C++ from `__result` -- no explicit `-> std::vector<T>`
  trailing return type needed

### With filter (Phase 2)

```python
# [x for x in items if x > 0]
```

```cpp
[&]() {
    std::vector<int32_t> __result;
    auto& __obj_1 = items;
    auto __beg_1 = __obj_1.begin();
    auto __end_1 = __obj_1.end();
    for (; __beg_1 != __end_1; ++__beg_1) {
        auto& x = *__beg_1;
        if (x > 0) {
            __result.push_back(x);
        }
    }
    return __result;
}()
```

### With tuple unpacking (Phase 3)

```python
# [v for k, v in pairs]
```

```cpp
[&]() {
    std::vector<ValueType> __result;
    auto& __obj_1 = pairs;
    auto __beg_1 = __obj_1.begin();
    auto __end_1 = __obj_1.end();
    for (; __beg_1 != __end_1; ++__beg_1) {
        auto& [k, v] = *__beg_1;
        __result.push_back(v);
    }
    return __result;
}()
```

C++17 structured bindings (`auto& [k, v]`) map directly to tuple unpacking.

### Iteration strategies

The codegen should reuse the existing for-loop iteration strategies:

| Iterable type | Strategy | Same as `for x in ...` |
|---------------|----------|------------------------|
| `list[T]`, `Array[T,N]`, `Span[T]` | `begin()/end()` range | Yes |
| `range(N)` | Counter loop (`for i = 0; i < N; ++i`) | Yes |
| `dict[K,V]` | Native iteration over ordered_map | Yes |
| `Iterator[T]` / `Iterable[T]` | `__next_opt__` while-loop | Yes |

The existing `_gen_for_each` already handles all these cases. The comprehension codegen
wraps the same logic inside the IIFE.

---

## Phase 4: Annotation Propagation

When the user provides an explicit type annotation:

```python
result: list[Int32] = [x for x in items]
```

The expected element type (`Int32`) propagates into the comprehension's element
expression via `analyze_expr_with_hint`, enabling coercions (e.g., Int32 -> int,
Int32 -> Int64). This follows the same pattern as list literal annotation propagation.

Without annotation, the element type is inferred purely from the element expression
(bottom-up). With annotation, the expected type also flows top-down (bi-directional).

Works in all hint-providing contexts: variable declarations, return statements
(`Own[list[T]]`), and function arguments. Incompatible annotations produce a clear
error: "Type mismatch in list comprehension element".

---

## Phase 5: Optimizations

### Sized iterables -> reserve

For Sized iterables (list, array, dict, span, dict views), `reserve()` is emitted
before the loop to pre-allocate the result vector:

```cpp
[&]() {
    std::vector<int32_t> __result;
    auto& __obj_1 = items;
    __result.reserve(__obj_1.size());
    auto __beg_1 = __obj_1.begin();
    // ...
}()
```

For `range()` iterables, range arguments are hoisted into temporaries (to avoid
double-evaluation when used in both `reserve()` and the loop), with a guard
against negative values:

```cpp
// range(N) with FixedInt:
const int32_t __stop_0 = N;
if (__stop_0 > 0) __result.reserve(static_cast<size_t>(__stop_0));
for (int32_t i = 0; i < __stop_0; ++i) { ... }

// range(start, stop) with FixedInt:
const int32_t __start_0 = start;
const int32_t __stop_1 = stop;
if (__stop_1 > __start_0) __result.reserve(static_cast<size_t>(__stop_1 - __start_0));
for (int32_t i = __start_0; i < __stop_1; ++i) { ... }

// range(N) with BigInt (uses checked conversion):
{ size_t __sz; if (__stop_0.to_size_checked(__sz)) __result.reserve(__sz); }
```

The `reserve()` is a no-regret optimization even with filters (over-reserves but
never under-reserves). For 3-arg `range(start, stop, step)`, no `reserve()` is
emitted since the element count cannot be cheaply computed.

### range(N) with literal N -> Array (future)

A further optimization could produce `Array[Int32, N]` instead of `list[Int32]`
when N is a compile-time literal and there is no filter. This would eliminate heap
allocation entirely. Deferred to Future Extensions due to sema/codegen complexity
(type changes, PendingListType interaction, annotation conflicts).

---

## Dict Comprehension (Future)

Dict comprehensions share the generator infrastructure but produce `dict[K, V]`:

```python
# {k: v * 2 for k, v in items.items() if v > 0}
```

### AST Node

```python
@dataclass
class TpyDictComprehension(TpyExpr):
    """Dict comprehension: {key_expr: value_expr for var in iterable if cond}"""
    key_expr: TpyExpr
    value_expr: TpyExpr
    generator: TpyComprehensionGenerator
```

### Codegen

```cpp
[&]() {
    tpy::ordered_map<KeyType, ValueType> __result;
    for (auto& [k, v] : items) {
        if (v > 0) {
            __result.insert_or_assign(k, v * 2);
        }
    }
    return __result;
}()
```

Same IIFE pattern, same generator reuse. Depends on tuple unpacking in generators
(Phase 3) for the common `for k, v in d.items()` pattern.

**Dependencies**: Dict type (done), tuple unpacking in generators (Phase 3).

---

## Generator Expressions (Future)

Generator expressions look like list comprehensions without brackets:

```python
total = sum(x * x for x in items)
any_negative = any(x < 0 for x in items)
```

In Python, these produce lazy iterators. In TPy, the interesting optimization is that
when passed directly to consuming builtins (`sum`, `any`, `all`, `min`, `max`), no
intermediate collection is needed -- the loop can be fused into the consumer.

### Codegen approaches (tiered)

**Tier 1: Fused IIFE** (primary, zero allocation) -- when the generator is the sole
argument to a known builtin (`sum`, `any`, `all`, `min`, `max`, `list`, `dict`),
fuse the loop into the consumer:

```python
total = sum(x * x for x in items)
```

```cpp
auto total = [&]() {
    int64_t __acc = 0;
    for (auto& x : items) {
        __acc += x * x;
    }
    return __acc;
}();
```

Each builtin has a known accumulation pattern:

| Builtin | Accumulator init | Accumulation | Short-circuit |
|---------|-----------------|--------------|---------------|
| `sum` | `0` (element type) | `__acc += expr` | No |
| `any` | `false` | `if (expr) return true` | Yes |
| `all` | `true` | `if (!expr) return false` | Yes |
| `min` | first element | `if (expr < __acc) __acc = expr` | No |
| `max` | first element | `if (expr > __acc) __acc = expr` | No |
| `list` | `vector<T>{}` | `push_back(expr)` | No |

This covers the vast majority of real-world generator expression usage.

**Tier 2: Lambda to template function** (future, for user-defined consumers) --
the generator is split into an iterable + a transform lambda passed to a templated
consumer:

```python
result = consume(x * x for x in items)
```

```cpp
auto result = consume(items, [](auto& x) { return x * x; });
```

This requires the consumer to accept a callable parameter, which depends on
`Callable` type support (D2) and closures (D1). Also doesn't naturally express
filters without a second lambda. Deferred.

**Tier 3: Materialize fallback** -- when the generator is passed to an unknown
consumer or stored in a variable, materialize to `list[T]` (equivalent to wrapping
in `list(...)`). A warning could suggest using an explicit list comprehension.

**Tier 4: Iterator object** -- generate a state-machine iterator class. Most
general but complex (essentially the generators/yield feature, F3). Deferred.

Recommendation: implement Tier 1 + Tier 3. Tier 1 covers the high-value cases
with zero overhead and zero new runtime infrastructure. Tier 3 is the safe fallback.

**Dependencies**: List comprehensions (this design). Builtin function awareness in sema.

---

## Error Cases

| Error | Example | Message |
|-------|---------|---------|
| Nested generators | `[x+y for x in a for y in b]` | "Nested comprehensions not yet supported" |
| Async comprehension | `[x async for x in aiter]` | "Async comprehensions not yet supported" |
| Non-iterable source | `[x for x in 42]` | "Type 'Int32' is not iterable" (existing error) |
| Incompatible annotation | `x: list[Int32] = [s for s in strs]` | "Type mismatch in list comprehension element" |

---

## Test Plan

```
tests/cases/list/
    list_comp_basic/           # Phase 1: [x*2 for x in range(5)], [p.name for p in items]
    list_comp_filter/          # Phase 2: [x for x in items if x > 0]
    list_comp_unpack/          # Phase 3: [v for k, v in pairs]
    list_comp_annotation/      # Phase 4: annotation propagation (Int32->int, Int32->Int64)
    list_comp_types/           # Various element types: records, Optional, str
    list_comp_nested_expr/     # Complex element expressions: method calls, f-strings
    list_comp_in_context/      # Comprehension as function arg, return value, in print()
    error_list_comp_annotation/    # Error: incompatible annotation type
    error_list_comp_nested/    # Error: nested generators
    error_list_comp_not_iterable/  # Error: non-iterable source
    error_list_comp_unpack_count/  # Error: unpack count mismatch
    error_list_comp_unpack_non_tuple/  # Error: unpack on non-tuple
```
