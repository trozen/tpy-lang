# Comprehensions

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | List comprehension: `[expr for var in iterable]`, single generator, no filter | Done |
| 2 | Filter clause: `[expr for var in iterable if cond]` | Done |
| 3 | Tuple unpacking in generator: `[v for k, v in pairs]` | Done |
| 4 | Annotation propagation: `result: list[int32] = [x for x in items]` | Done |
| 5 | Optimizations: Sized iterables -> `reserve()`, range -> `reserve()` | Done |
| 6 | Dict comprehension: `{key: value for var in iterable if cond}` | Done |
| 7 | Set comprehension: `{expr for var in iterable if cond}` | Done |
| 8 | `range(N)` -> Array: compile-time-known size produces `std::array<T, N>` | Done |
| 9 | Generator expressions: `(expr for x in iterable)` -> a generator frame | Done |

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
from tpy import int32

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
where each element is copied into the result via `push_back` (the source is
borrowed, so the loop var is a borrow into a live element). This is consistent
with how a for-loop + `append` would behave -- the result list owns its elements.

When the source instead *yields* `Own[T]` (a generator of owned values, or an
`Iterable[Own[T]]`), a bare loop-var element is *moved* into the result,
mirroring the consuming `for`+`append`: `[node for node in g()]` over
`g() -> Iterator[Own[Node]]` emits `push_back(std::move(node))`. So `@nocopy`
owned elements collect without a copy error, and the storage-copy warning is
suppressed. The rule is **the comprehension's last-evaluated sink** is
structurally the last use (the loop var is rebound each iteration; earlier reads
are sequenced before it), so it moves a bare owned var unconditionally:

- list/set: the element is the last sink -- moves even after a filter
  (`[x for x in g() if p(x)]` moves; the filter ran first).
- dict: the *value* is the last sink and moves; the *key* (evaluated first) is
  sequenced into a local before the value move so `{node.id: node}` does not read
  a moved-from element. The key moves only when it is the genuine last use --
  `{node: node.id}` keeps the key copy (the value reads the element after it).

An earlier or derived sink (`x.field`, `f(x)`, the dict key when a later sink
reads the var) and a borrowed source still copy.

For `@nocopy` types over a *borrowed* source, the element expression must produce
an owned value (e.g. `[make_thing(x) for x in inputs]` where `make_thing` returns
`Own[Thing]`). Copying a `@nocopy` loop variable into the result is an error,
same as `items.append(nocopy_ref)` would be.

### Iteration loan and loop-variable writes

A list, set or dict comprehension borrows its source exactly as a `for` over it
does: `register_iteration_loans` (`tpyc/sema/iter_loans.py`) files the ITER
borrow when `comprehension_scope` opens, and it is held while the conditions and
the element run. So growing the source there warns (`Mutation of 'xs' while
iterating over it`, or `Passing borrowed container 'xs' to non-readonly
parameter` through a mutating callee), and the borrow ends with the
comprehension. An enclosing loop's borrow of the same storage is merged, not
replaced (`for v in xss[0]:` around `[... for w in xss ...]` keeps the element
borrow), and the scope's exit puts back exactly the borrows held on entry.

A source rooted at a name spelled like one of the targets (`[grow(c) for c in
c]`) is the ENCLOSING binding of that name, which the comprehension's own code
cannot reach by name, so no loan is filed on it: inside the scope the name is
the target, and a write through the target is not growth of the source.

The loop variable records no source: a write through it does not reach a
parameter source or an enclosing loop variable, so `[c.bump(1) for c in cs]`
over a parameter keeps `cs` const and fails the build
(`BUGS.md#comprehension-loop-var-mutation-not-propagated`, whose design notes
say why the name-keyed fact the `for` statement uses is not enough here).

### Edge case: empty iterable

`[f(x) for x in empty_list]` where `empty_list: list[int32]` works fine -- the
loop variable type is `int32` (from the list's element type), the element expression
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
result: list[int32] = [x for x in items]
```

The expected element type (`int32`) propagates into the comprehension's element
expression via `analyze_expr_with_hint`, enabling coercions (e.g., int32 -> int,
int32 -> int64). This follows the same pattern as list literal annotation propagation.

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
parameter, explicitly annotated as `list[T]`, or when the element expression
reaches an `@error_return` callable (the unwrap's `return`/`goto` cannot cross
the array builder's lambda).

Codegen strategies (all via `tpy::array_from_index<T, N>(f)` -- aggregate
construction, element expression evaluated exactly N times left-to-right,
each result constructed in place; no element default ctor or assignment):
- `range(...)` with literal args: per-index lambda binds the loop var as
  `start + i * step` index arithmetic
- `Array[T,N]` source: stmt-expr prelude borrows the source once, the lambda
  binds from `__obj[i]` (random access); tuple-unpack shares the inline
  unpack helper with the vector path

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
squares: list[int32] = list(x * x for x in range(5))
```

### Implementation

A generator expression is an anonymous GENERATOR FUNCTION, created where the
expression is written and compiled by the one resumable-frame emitter every
`def` generator goes through. There is no separate genexpr producer.

```python
result = sum_positive(x * x + k for x in data if x > 0)
```

Sema builds the function at the expression (`_analyze_genexpr_function`,
`tpyc/sema/expressions.py`):

```python
def __genexpr_f_1(__src, k):        # source first, then the captures
    for x in __src:
        if x > 0:
            yield x * x + k
```

and analyzes it ONCE, through the ordinary `_analyze_function` lifecycle, under
a function state of its own (the enclosing function's state is set aside and
put back as itself). Every per-function fact the frame emitter reads -- frame
locals, const verdicts, mutation facts, deferred yield-borrow checks -- comes
from that lifecycle; nothing is filled in by hand. Three things are specific to
a genexpr:

- **The yield type is inferred** from the element at its one `yield`; the
  function is registered once the body has settled it.
- **The source is analyzed at the creation site**, in the enclosing function's
  flow, because that is where it is evaluated (CPython's eager
  `iter(outermost)`).
- **Captures are params taken by reference.** A capture is every enclosing
  name the element and filters read (`self` and a nested def's own captures
  included). The frame holds each in a deduced-type forwarding slot, which an
  lvalue argument makes a reference field of whatever C++ type the enclosing
  variable has -- so the storage form of that variable stays the enclosing
  body's business, and a rebind between two pulls is visible in the body.

Creating the frame hands the source and the captures over the way a call hands
over its arguments, and is checked as one, with the three checks every call
site runs:

- **Mutation facts.** A body that mutates through its loop var or through a
  capture keeps the enclosing param a mutable borrow, except when its source is an
  ENCLOSING loop var (`BUGS.md#genexpr-over-loop-var-param-stays-const`).
- **Conflicts with live loans.** A body that may grow or rewrite a capture on
  which a loan is live where the expression is written (`for v in xs: n +=
  sum(1 for _ in range(1) if grow(xs))`) warns *Borrowed container 'xs' is
  mutated by a generator expression that captures it*. A str/bytes view
  borrowed out of such a capture is demoted to an owned copy. The frame is
  analyzed right there, so its own facts stand in for its signature: a
  capture that the body neither writes nor hands to a call (as an argument or
  as the receiver of `self.m()`) keeps its views. A write through a callee is
  judged once Phase 2 has the callee's facts.
- **The source's own iteration.** Inside the frame, the source param and a
  name the body reads can reach one container: a capture, or a module global
  the body reads directly. So the frame's loop holds the source's iteration
  loans (`iterated_storage`) on that name as well as its own loan on the
  source param. A body that grows its own source (`sum(v for v in xs if
  grow(xs))`, `v + xs.pop()`) gets the verdict of a `for` over that source,
  at the mutating line.

```cpp
template <typename F_k>
struct __genexpr_f_1_frame : ::tpy::next_iter_mixin<__genexpr_f_1_frame<F_k>, int32_t> {
    int32_t __state;                     // every frame carries it; unread in this form
    const std::vector<int32_t>& __src;   // borrowed source
    F_k k;                               // deduced: `const int32_t&`
    int32_t x;
    ::tpy::frame_loop_slot<...> __for_it_0, __for_end_0;
    __genexpr_f_1_frame(const std::vector<int32_t>& __src, F_k&& k_)
        : __state(S_INITIAL), __src(__src), k(std::forward<F_k>(k_))
        { __for_it_0.emplace((this->__src).begin()); __for_end_0.emplace((this->__src).end()); }
    std::expected<int32_t, ::tpy::StopIteration> __next__() {
        while (!((*__for_it_0) == (*__for_end_0))) {
            x = *((*__for_it_0))++;
            if ((x > 0)) { return x * x + k; }
        }
        return ::tpy::make_unexpected(::tpy::StopIteration{});
    }
};
auto result = sum_positive(__genexpr_f_1(data, k));
```

**Where the frame is emitted.** Only the body that creates a genexpr can name
its frame (the function is never exported), so the frame goes where that body
goes. A body that is a plain definition in the `.cpp` -- a non-template
function, a larger method or constructor of a non-generic record, the module
init -- gets the whole frame (struct, `__next__`, factory) in an anonymous
namespace directly above it, and the header never mentions it. A body emitted
in a header (a generic function or record, a function templated by a
protocol-typed param, a small method) keeps the frame in the header, and so
does a generator or `async` body, whose own frame struct may hold the genexpr's
by value. There it is placed like any generator frame: a TEMPLATE frame (a
capture, a deduced source, the owner's type params) is inline in the header
whole, since another module calling that body instantiates it; a non-template
one has its struct in the header, its factory in the `.cpp` and its `__next__`
in `<mod>_inl.hpp` -- or in the `.cpp` when the module emits no such file. `CodeGenerator._genexpr_in_source` asks the same partition predicates
the drivers place the bodies with. The struct is named after the function with
a `_frame` suffix (`__genexpr_<function>_<n>_frame`), so it reads apart from a
`def` generator's `__gen_<name>`.

Key properties:
- **General** -- works with any function accepting `Iterable[T]`, not just builtins
- **Lazy** -- elements computed on demand, no intermediate collection
- **Zero allocation** -- the frame lives where the expression is consumed
- **One producer** -- a genexpr behaves exactly like the hand-written generator
  it is equivalent to, because it is one

### The source

| Source | How the frame takes it |
|--------|------------------------|
| `range(...)` | The bounds are evaluated at creation and go in BY VALUE; the body loops over a range of them, which the frame walks with plain counters |
| Stable lvalue container (name, field, subscript) | Its own type, by reference; walked by begin/end, so a reference element -- or a record unpacked from a tuple element -- aliases the source |
| Any rvalue (literal, dict view, combinator, generator call, `Own[container]` call), or a non-container iterable | A deduced slot the frame OWNS, built IN PLACE from a factory: `f(std::in_place, [&] { return <source>; }, captures...)` initializes the field from the prvalue the factory returns (guaranteed elision) |

The source classification (`_source_route` in `tpyc/thir/lower/comprehensions.py`)
is shared with the list/set/dict comprehensions: a source one form iterates, the
other does too. The in-place form never moves its source, makes no allocation,
and admits a source with NO move constructor -- a combinator owning a
user-iterable temporary with a separate iterator. That is the one source that
pins its frame; at an owning boundary (another lazy combinator taking the
genexpr as its rvalue argument) it is a located reject,
`genexpr.pinned_into_owning` (`THIRGenExpr.pinned_source`,
BUGS.md#separate-iter-temp-no-flush-slot). Every other frame is movable until
its first pull, like its source, so `enumerate(x * 2 for x in xs)` may move it.

A container LITERAL local of the enclosing function (`xs = [1, 2, 3]`) has not
settled Array-vs-list when the genexpr over it is analyzed; the genexpr
function's recorded types are handed up to the enclosing function's
finalization, so the literal stays a `std::array` and the frame's param follows
it. (Inside a nested def the deduced slot stands in: a nested def cannot hand
an unsettled type of ITS enclosing function any further up.)

A local binding of a genexpr is not lowered. One asymmetry with the
comprehension route: the genexpr source is lowered with no arg-temp right, so a
source call whose argument needs a hoisted temp rejects
(BUGS.md#genexpr-source-needs-arg-temp).

### Narrowing of a captured name

The body runs at each pull, later than the creation. A narrowing proved at the
creation holds in the body only where nothing can rebind the name between two
pulls (`_capture_may_be_rebound`): an argument-position consumer (`sum`, `any`,
`list`, `str.join`) pulls to the end inside one expression, so the capture is
taken at its narrowed type (`const int32_t&` bound to `*k`). A `for` head whose
loop body assigns the name, or a closure that writes it, breaks that: the
capture is the whole `T | None`, the body reads it through the checked deref,
and sema warns. A non-Optional union narrowed in that position is a compile
error.

A lazy value BOUND to a name outlives its statement too: `g = relay(x * k for x
in xs)`, or a lazy combinator over the genexpr. The statement that binds it
scans the rest of the enclosing body (`_check_retained_genexprs` in
`tpyc/sema/statements.py`; the module's top-level statements at module level)
for a rebind of a narrowed capture while the value is still live -- from the
binding, by source position so a rebind later on the same line counts, up to
the last read of the kept name, widened to the end of any loop that read sits
in. A name the value is handed on to (`h = g`) is kept as well, a `nonlocal`
write in a nested def counts as a rebind, and a nested def READING the kept
name leaves the range open. A rebind inside that range is a located error
naming the fix (bind the narrowed value to a local first). The scan cannot see
types of statements it has not reached yet, so ANY statement that reads the
kept name and binds another counts as handing it on, a consuming one included
(BUGS.md#genexpr-kept-scan-consumer-binding).

### Emit shape

A genexpr frame has one resume point and it is the loop head, so
`_single_loop_plan` (`tpyc/codegen_cpp/gen_async.py`) reads that shape off the
CFG before anything is rendered -- one loop advance, an entry that falls into
its head, every other case an empty chain of `Fall` blocks onto it -- and the
body is then emitted once as a plain loop with no `switch (__state)`: the
state-transition sites (a transfer to a case, a yield's resume store, the
terminal store, the loop advance) consult the active `_LoopForm`, so the
stores and the redundant `continue`s are never written; `__state` keeps only what still has to be
remembered. Over a borrowed container the begin/end pair is seeded in the
CONSTRUCTOR (the iterators point outside the frame, so this neither pins it nor
changes behaviour) and asking the source again after exhaustion is free, so
nothing is remembered at all. A frame that owns its source seeds at the first
pull -- its iterators would point into the frame, which may still move -- and
records exhaustion, since CPython never touches a finished source again.
Measured against the closure render this replaced, the same generated sources
built by hand at `-O3 -DNDEBUG` so both sides share one command line
(`scripts/perf/generator_frames.py`, gcc-14 / clang-20, ms, closure -> frame):
`sum` 76 -> 78 / 87 -> 86, a user `Iterable` consumer 77 -> 77 / 86 -> 85, `any`
70 -> 72 / 103 -> 106. The same frames WITHOUT this form measured `sum` 79 /
86, user 84 / 99, `any` 85 / 89 -- the form is worth 8% and 15% on the two gcc
rows and 14% on clang's user row. (TODO.md's generator-frames perf entry quotes
`uv run tpy` runs from another day; its absolute figures differ by a few ms.)
Two gcc layout facts are encoded in the emitter: the terminal return sits AFTER
the loop (12% on an out-of-line consumer), and a filter's redundant trailing
`else { continue; }` is dropped (10%).

The form is written against the frame's CFG shape but enabled for genexpr
frames only; a `def` generator of the same shape still emits the state switch
(TODO.md "The single-loop emit form for `def` generators").

### Rvalue binding

Generator expressions produce rvalue temporaries. Two mechanisms handle this:

1. **User-defined functions** with protocol template params (`T_items& items`):
   `is_temporary_expr` recognizes `TpyGeneratorExpression`, triggering temp
   hoisting (`auto __tmp_N = <frame creation>;`).

2. **Runtime functions** (`str_join`, `list_extend`, `construct`, `dict_construct`):
   Non-range overloads in `iterable_ops.hpp` use forwarding references
   (`Container&&`) to accept both lvalues and rvalue temporaries directly.

### Type system

`GenExprType(element_type)` is an internal-only type (not user-facing). It
satisfies `Iterable[T]` and `Iterator[T]` protocols via special cases in
`protocols.py`. The C++ type is always `auto` (the frame of the function sema
builds for the expression, deduced where it is bound).

### Future optimization: consumer fusion for builtins

As a transparent optimization, the compiler could fuse `builtin(genexpr)`
patterns into a single loop, with no generator object at all:

```python
total = sum(x * x for x in range(100))
# -> fused accumulation loop, no frame
```

This is deferred -- the frame is correct and efficient enough as baseline. Fusion can be added later without changing semantics.

---

## Error Cases

| Error | Example | Message |
|-------|---------|---------|
| Nested generators | `[x+y for x in a for y in b]` | "Nested comprehensions not yet supported" |
| Async comprehension | `[x async for x in aiter]` | "Async comprehensions not yet supported" |
| Non-iterable source | `[x for x in 42]` | "Type 'int32' is not iterable" (existing error) |
| Incompatible annotation | `x: list[int32] = [s for s in strs]` | "Type mismatch in list comprehension element" |

---

## Test Plan

```
tests/cases/list/
    list_comp_basic/           # Phase 1: [x*2 for x in range(5)], [p.name for p in items]
    list_comp_filter/          # Phase 2: [x for x in items if x > 0]
    list_comp_unpack/          # Phase 3: [v for k, v in pairs]
    list_comp_annotation/      # Phase 4: annotation propagation (int32->int, int32->int64)
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
    dict_comp_annotation/      # Phase 6: annotation propagation (int32->int, int32->int64)
    error_dict_comp_nested/    # Error: nested generators
    error_dict_comp_not_iterable/      # Error: non-iterable source
    error_dict_comp_unpack_count/      # Error: unpack count mismatch
    error_dict_comp_unpack_non_tuple/  # Error: unpack on non-tuple

tests/cases/set/
    set_comp_basic/            # Phase 7: range->set, list->set, dedup, 2-arg range
    set_comp_filter/           # Phase 7: single filter, string filtering
    set_comp_unpack/           # Phase 7: tuple unpacking from dict.items(), list of tuples
    set_comp_annotation/       # Phase 7: annotation propagation (int32->int64, int32->int)
    error_set_comp_nested/     # Error: nested generators
    error_set_comp_not_iterable/       # Error: non-iterable source
    error_set_comp_unpack_count/       # Error: unpack count mismatch
    error_set_comp_unpack_non_tuple/   # Error: unpack on non-tuple
```
