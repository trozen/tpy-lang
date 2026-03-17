# Comprehensions

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | List comprehension: `[expr for var in iterable]`, single generator, no filter | Done |
| 2 | Filter clause: `[expr for var in iterable if cond]` | Done |
| 3 | Tuple unpacking in generator: `[v for k, v in pairs]` | Done |
| 4 | Annotation propagation: `result: list[Int32] = [x for x in items]` | Done |
| 5 | Optimizations: Sized iterables -> `reserve()`, range -> `reserve()` | Done |
| 6 | Dict comprehension: `{key: value for var in iterable if cond}` | Done |
| 7 | Set comprehension: `{expr for var in iterable if cond}` | Done |
| 8 | `range(N)` -> Array: compile-time-known size produces `std::array<T, N>` | Done |
| 9 | Generator expressions: `(expr for x in iterable)` -> lazy `make_generator` wrapper | Done |

### Future Extensions

| Feature | Notes |
|---------|-------|
| `Span[T, N]` -> Array | When fixed-size span is available, `[x for x in span]` can produce `std::array<T, N>` |
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
| `Iterator[T]` / `Iterable[T]` | `__next__` while-loop (`std::expected`) | Yes |

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

### Compile-time Array promotion (Phase 8)

When a list comprehension's output size is known at compile time and there are no
filter conditions, the result type is `Array[T, N]` instead of `list[T]`, producing
`std::array<T, N>` with zero heap allocation.

Supported sources:
- `range(N)` with literal N >= 0
- `range(start, stop)` with both literal, stop >= start
- `range(start, stop, step)` with all literal, step != 0 (positive and negative)
- `Array[T, N]` source (size propagates)

Uses `PendingListType` for deferred resolution -- automatically falls back to
`list[T]` if the variable is mutated (e.g. `.append()`), passed to a `list[T]`
parameter, or explicitly annotated as `list[T]`.

Codegen strategies:
- `range(N)`: simple counter loop with indexed assignment
- `range(start, stop)`: counter loop with start offset
- `range(start, stop, step)` and `Array[T,N]`: begin/end iterator with index counter

---

## Dict Comprehension (Phase 6)

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
    result_key_type: TpyType | None = None    # set by sema
    result_value_type: TpyType | None = None  # set by sema
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

Same IIFE pattern, same generator reuse. No `reserve()` since `ordered_map` doesn't
expose it. Uses `insert_or_assign` for correct Python overwrite semantics (later keys
replace earlier ones).

Supports all iteration strategies (range, begin/end), filters, tuple unpacking,
and annotation propagation (both key and value types independently).

---

## Generator Expressions

Generator expressions produce lazy iterators consumed by functions accepting
`Iterable[T]`:

```python
total = sum_items(x * x for x in items)
joined = ", ".join(str(x) for x in nums)
squares: list[Int32] = list(x * x for x in range(5))
```

### Implementation

The codegen produces a mutable C++ lambda returning `optional<T>`, wrapped in
`tpy::generator_wrapper<T, F>` via `tpy::make_generator<T>(lambda)`. The
wrapper provides `__next__()` (returning `std::expected<T, StopIteration>`) and
`__iter__()`, integrating with the standard iterator infrastructure.

```python
result = sum_positive(x * x for x in data if x > 0)
```

```cpp
auto __tmp_1 = tpy::make_generator<int32_t>(
    [&, __beg = data.begin(), __end = data.end()]() mutable
        -> std::optional<int32_t> {
        while (__beg != __end) {
            int32_t x = *__beg++;
            if (x > 0) { return std::optional<int32_t>(x * x); }
        }
        return std::nullopt;
    }
);
auto result = sum_positive(__tmp_1);
```

Key properties:
- **General** -- works with any function accepting `Iterable[T]`, not just builtins
- **Lazy** -- elements computed on demand, no intermediate collection
- **Zero allocation** -- lambda + wrapper live on stack
- **Zero-cost with templates** -- `Iterable[T]` params compile as C++ templates,
  so the compiler inlines through `make_generator` + lambda

### Codegen strategies

| Source | Strategy | State |
|--------|----------|-------|
| `range(N)`, `range(start, stop)` | Counter lambda | `__i`, `__stop` init-captures |
| `range(start, stop, step)` | IIFE + Range iterator | `tpy::Range<T>` begin/end captures |
| Containers (list, dict, etc.) | IIFE + begin/end captures | Iterator init-captures |

### Rvalue binding

Generator expressions produce rvalue temporaries. Two mechanisms handle this:

1. **User-defined functions** with protocol template params (`T_items& items`):
   `is_temporary_expr` recognizes `TpyGeneratorExpression`, triggering temp
   hoisting (`auto __tmp_N = make_generator<T>(...);`).

2. **Runtime functions** (`str_join`, `list_extend`, `from_range`, `dict_from_pairs`):
   Non-range overloads in `iterable_ops.hpp` use forwarding references
   (`Container&&`) to accept both lvalues and rvalue temporaries directly.

### Type system

`GenExprType(element_type)` is an internal-only type (not user-facing). It
satisfies `Iterable[T]` and `Iterator[T]` protocols via special cases in
`protocols.py`. The C++ type is always `auto` (deduced from `make_generator`).

### Future optimization: fused IIFE for builtins

As a transparent optimization, the compiler could fuse `builtin(genexpr)`
patterns into a single IIFE, bypassing the generator wrapper:

```python
total = sum(x * x for x in range(100))
# -> fused accumulation loop, no make_generator
```

This is deferred -- the lambda approach is correct and efficient enough as
baseline. Fusion can be added later without changing semantics.

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

tests/cases/dict/
    dict_comp_basic/           # Phase 6: range->dict, list->dict, dict rebuild, 2-arg range
    dict_comp_filter/          # Phase 6: single/multiple filter conditions
    dict_comp_unpack/          # Phase 6: tuple unpacking from dict.items(), list of tuples
    dict_comp_annotation/      # Phase 6: annotation propagation (Int32->int, Int32->Int64)
    error_dict_comp_nested/    # Error: nested generators
    error_dict_comp_not_iterable/      # Error: non-iterable source
    error_dict_comp_unpack_count/      # Error: unpack count mismatch
    error_dict_comp_unpack_non_tuple/  # Error: unpack on non-tuple

tests/cases/set/
    set_comp_basic/            # Phase 7: range->set, list->set, dedup, 2-arg range
    set_comp_filter/           # Phase 7: single filter, string filtering
    set_comp_unpack/           # Phase 7: tuple unpacking from dict.items(), list of tuples
    set_comp_annotation/       # Phase 7: annotation propagation (Int32->Int64, Int32->int)
    error_set_comp_nested/     # Error: nested generators
    error_set_comp_not_iterable/       # Error: non-iterable source
    error_set_comp_unpack_count/       # Error: unpack count mismatch
    error_set_comp_unpack_non_tuple/   # Error: unpack on non-tuple
```
