# Comprehensions

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | List comprehension: `[expr for var in iterable]` | Done |
| 2 | Filter clauses: `[expr for var in iterable if cond]` | Done |
| 3 | Tuple unpacking in a clause: `[v for k, v in pairs]` | Done |
| 4 | Annotation propagation: `result: list[int32] = [x for x in items]` | Done |
| 5 | Optimizations: Sized iterables -> `reserve()`, range -> `reserve()` | Done |
| 6 | Dict comprehension: `{key: value for var in iterable if cond}` | Done |
| 7 | Set comprehension: `{expr for var in iterable if cond}` | Done |
| 8 | `range(N)` -> Array: compile-time-known size produces `std::array<T, N>` | Done |
| 9 | Generator expressions: `(expr for x in iterable)` -> a generator frame | Done |
| 10 | Several `for` clauses: `[x for row in grid for x in row]`, every form | Done |

### Future Extensions

| Feature | Notes |
|---------|-------|
| `Span[T, N]` -> Array | When fixed-size span is available, `[x for x in span]` can produce `std::array<T, N>` |
| Multi-clause Array demotion | `[i * j for i in range(3) for j in range(4)]` has a compile-time size too (TODO.md "A multi-clause list comprehension never demotes to an `Array`") |
| Lifting the clause refusals | An owned-yielding outer clause, a walrus binding a reference, a name two clauses bind, a rebound capture an inner genexpr clause iterates (TODO.md "Lift: ..." entries) |
| Async comprehensions | `[x async for x in aiter]` |

### Known Limitations

| Area | Detail |
|------|--------|
| Name bound by two `for` clauses | `[row for row in rows for row in row]` is refused; `_` is exempt while nothing in the comprehension reads it |
| Owned-yielding outer clause | Only the innermost clause of a list / set / dict comprehension may iterate an `Iterator[Own[T]]` source; a generator expression's clause 0 never may (its inner clauses are the frame body's own `for` loops, which may) |
| Walrus binding a reference | A walrus whose target can hold a reference is refused inside a list / set / dict comprehension, whatever it iterates, though CPython runs it (see Error Cases) |
| Genexpr inner-clause capture rebound | Refused while the frame may still pull ("bind it to a local first") |
| No starred unpacking | `[*a, *b]` inside comprehensions is not supported |

A filter or inner iterable that reads a name a later clause binds is refused
too, but that refuses no working program: CPython raises `UnboundLocalError`
there.

---

## Overview

List comprehensions are one of Python's most distinctive features -- concise, readable
expressions that build lists from iterables with optional filtering.

```python
from tpy import int32

squares = [x * x for x in range(10)]
evens = [x for x in items if x % 2 == 0]
names = [p.name for p in people]
flat = [x for row in grid for x in row]
```

They are used in 16+ files of the TPy compiler source and are a prerequisite for
self-hosting. More importantly, they're idiomatic Python that users expect to work.

All Python comprehension forms (`ListComp`, `DictComp`, `SetComp`, `GeneratorExp`)
share the same list of `comprehension` clauses in the AST, and TPy keeps that
shape: one clause model, one analysis, one source classification for every form.

---

## Design Principles

1. **Expression semantics via a statement expression**: a comprehension is an
   expression whose body is a loop. It renders as a GCC statement expression
   (`({ ...; std::move(__result); })`) whose body is ordinary THIR statements, so
   it slots into any expression context -- assignments, arguments, returns, a
   constructor's member init.

2. **The `for` statement's loops**: each clause's loop is the loop node the `for`
   statement builds over the same source (`build_loop_node`,
   `tpyc/thir/lower/statements.py`), rendered by the statement emitter. A source
   one form iterates, the other does too, with the same render.

3. **Shared clause model**: `TpyComprehensionGenerator` is one clause; every
   comprehension kind and generator expressions carry a list of them.

4. **Optimize where obvious**: `range(N)` with literal N can produce `Array[T, N]`
   (zero heap allocation); a sized single-clause source `reserve()`s.

5. **CPython compatible**: comprehensions are native Python. No stubs or
   `no_cpython.txt` needed.

---

## Python AST Structure

All comprehension forms use the same `comprehension` clause node:

```python
# Python AST for: [x * 2 for row in grid if row for x in row if x > 0]
ListComp(
    elt=BinOp(left=Name('x'), op=Mult(), right=Constant(2)),
    generators=[
        comprehension(target=Name('row', Store()), iter=Name('grid', Load()),
                      ifs=[Name('row')], is_async=0),
        comprehension(target=Name('x', Store()), iter=Name('row', Load()),
                      ifs=[Compare(...)], is_async=0),
    ]
)
```

`DictComp` has `key` + `value` instead of `elt`. `GeneratorExp` is identical to
`ListComp` structurally. CPython evaluates clause 0's iterable in the enclosing
scope; everything else -- the filters, the inner iterables, the element -- runs
in the comprehension's own scope, where every clause's target is a variable.

---

## AST Nodes

### TpyComprehensionGenerator

One `for` clause, shared by every comprehension kind:

```python
@dataclass
class TpyComprehensionGenerator:
    """Single generator clause: for var in iterable [if cond]*"""
    var: str                           # loop variable (a fresh `__for_tup_N` holder when unpacking)
    iterable: TpyExpr
    conditions: list[TpyExpr]          # filters (may be empty)
    unpack_vars: list[str | None] | None = None   # `for k, v in`; `_` -> None
    const_loop_var: bool = False       # set by sema
    owns_elements: bool = False        # set by sema: the source yields Own[T]

    @property
    def targets(self) -> list[str]: ...    # the names this clause binds
```

### TpyListComprehension (and its siblings)

```python
@dataclass
class TpyListComprehension(TpyExpr):
    element_expr: TpyExpr
    generators: list[TpyComprehensionGenerator]   # outermost first
    result_elem_type: TpyType | None = None       # set by sema
```

`TpySetComprehension` has the same fields, `TpyDictComprehension` has
`key_expr` / `value_expr` and `result_key_type` / `result_value_type`, and
`TpyGeneratorExpression` adds the frame facts sema sets (the function it builds,
the capture reads, the creation call). `children()` lists every clause's iterable
and filters in the order CPython evaluates them (`comp_clause_children`), then
the element(s).

---

## Parser

`_parse_expr` handles all four node kinds through one helper:

```python
def _parse_comprehension_generators(self, node) -> list[TpyComprehensionGenerator]:
    if any(gen.is_async for gen in node.generators):
        raise ParseError("Async comprehensions not yet supported", node)
    return [self._parse_comprehension_generator(gen) for gen in node.generators]
```

`_parse_comprehension_generator` parses one clause: a `Name` target sets `var`,
a `Tuple` of names sets `unpack_vars` (a `_` element is `None`), anything else
is a `ParseError`. The parser accepts any number of clauses; the clause rules
are sema's.

---

## Semantic Analysis

### Analysis flow

`_analyze_elem_comprehension` / `_analyze_dict_comprehension`
(`tpyc/sema/expressions.py`):

1. `_check_comp_clause_names` refuses the clause-name shapes below.
2. Clause 0's iterable is analyzed in the ENCLOSING scope
   (`_resolve_comp_iterable`), its element type extracted, and
   `_stamp_comp_source_facts` records whether it yields `Own[T]`.
3. One `comprehension_scope()` opens, and `_enter_comp_clause(k)` recurses
   through the clauses: it files clause k's iteration loans
   (`_register_comp_iter_loans`), binds clause k's targets as loop variables,
   analyzes clause k's filters, then either analyzes clause k+1's iterable
   (inside the scope, with every earlier target visible) and recurses, or -- in
   the innermost clause -- analyzes the element(s) with the slot hint.
4. On the way out each clause credits a mutation of its loop variable to the
   comprehension target its source is rooted at
   (`credit_iter_source_mutation`, `reach=comp_targets`), so
   `[[c.bump() for c in row] for row in grid]` binds `row` mutably.

### Scoping

The comprehension's variables are not visible after it (Python 3 semantics).
All clauses share ONE scope, which gives two refusals:

- **A name two clauses bind** (`[row for row in rows for row in row]`) would be
  one variable the inner loop rebinds; each clause binds a fresh C++ local, so
  the shape is refused: *'row' is bound by two `for` clauses of this
  comprehension; rename one*. CPython accepts it. A `_` target is exempt
  while no inner iterable, filter or element reads it (`_comp_free_names`):
  then nothing can tell which clause bound it.
- **A read before the binding clause**: a filter, or an inner clause's
  iterable, reading a name a LATER clause binds reads that clause's variable
  before it is bound (CPython: `UnboundLocalError`), and is refused: *'x' is
  read before the `for` clause that binds it*. Clause 0's iterable is exempt:
  it runs in the enclosing scope and reads the enclosing name. A nested
  comprehension or lambda in a filter that binds the same name reads its own
  variable, not the later clause's (`_comp_free_names`).

A filter walrus binds in the ENCLOSING scope (PEP 572), so the inner sources,
the element and the code after the comprehension read it. Each clause's
source is classified while that clause lowers, against exactly the names in
scope there -- the earlier clauses' targets and the earlier filters' walrus
targets -- so `[c for i in r if (s := str(i)) for c in s]` iterates the
walrus target like any other name. Because the target
outlives the comprehension while an element may live in storage that dies
with it (clause 0 over an owning rvalue, or an inner clause over a temporary,
rebuilt per outer element), a walrus whose target can hold a REFERENCE -- a
reference type, an open type parameter, a pointer or borrowing view, or an
Optional / tuple / union holding one (`_may_keep_reference`) -- is refused
inside a list / set / dict comprehension whatever it iterates: *a walrus
inside a comprehension cannot bind 'last' to a reference to an object; use a
`for` loop*. Which source a reference comes from is not tracked, so a named
source is refused too. The check runs once in `_analyze_named_expr`, on the
target's type, before the binding branches split. A value-type target is
legal everywhere (a `str` slice is a view and is refused; a value tuple is
refused for now, BUGS.md#value-tuple-walrus-rejects). CPython runs every refused shape. A generator expression's
walrus binds a local of its frame
(BUGS.md#genexpr-walrus-target-stays-in-frame), so the rule does not arise
there.

### Type inference details

- **Element type**: the element expression analyzed with every clause's
  variables in scope.
- **Filter conditions**: no type restriction -- truthiness as in `if`/`while`.
- **Result type**: `ListType(result_elem_type)` -- `list[T]`, except the
  compile-time-sized single-clause case below.

### Ownership

`[p for p in people]` over `people: list[Person]` copies each element into the
result: the result list owns its elements, as a `for` + `append` would.

When the INNERMOST clause's source yields `Own[T]` (a generator of owned
values, or an `Iterable[Own[T]]`), a bare loop-var element is *moved* into the
result -- the consuming `for` + `append` move: `[node for node in g()]` emits
`push_back(std::move(node))`, so `@nocopy` owned elements collect without a
copy error. The comprehension's last-evaluated sink is the last use (the loop
var is rebound each iteration):

- list/set: the element moves, even after a filter (the filter ran first).
- dict: the *value* is the last sink and moves; the key is sequenced into a
  local first (see Dict codegen). The key moves only when it is the genuine
  last use -- `{node: node.id}` keeps the key copy.

A derived sink (`x.field`, `f(x)`) and a borrowed source still copy. Only the
innermost element is ever handed to the sink. An OUTER clause may iterate an
owned-yielding source too: its element stays in the iterator's step slot
(`auto&& ps = *__beg_0;`) for that iteration, and the inner clauses borrow it
(`auto& __obj_1 = ps;`), as a nested `for` statement does. `owns_elements` is
stamped per clause and stays the SOURCE's fact; the clause's name binds the
element's storage type (`_clause_bindings` strips the `Own`, as the `for`
statement's loop-var binding does), and only the innermost clause's sink reads
`owns_elements` to move. An outer element used as the result is copied per
inner iteration under the storage-copy warning, like a borrowed one.
A generator expression takes no owned source at clause 0. With several
clauses the refusal says so: *a generator expression cannot iterate a source
that yields owned values; use a list comprehension or a `for` loop*; a single
clause keeps its lowering refusal (`genexpr.owned_source`,
`iterators/error_genexpr_owned_generator_source`). Its inner clauses are
`for` loops of the frame body and follow the `for` statement's rule.

For `@nocopy` types over a *borrowed* source, the element expression must
produce an owned value (`[make_thing(x) for x in inputs]`); copying a `@nocopy`
loop variable into the result is an error, as `items.append(nocopy_ref)` is.

### Iteration loan and loop-variable writes

Each clause borrows its source exactly as a `for` over it does:
`register_iteration_loans` (`tpyc/sema/iter_loans.py`) files the ITER borrow
when the clause binds, and it is held for everything nested inside that clause
-- its filters, the inner clauses, the element. So growing a source there warns
(`Mutation of 'row' while iterating over it`, or `Passing borrowed container
'row' to non-readonly parameter` through a mutating callee), an inner source
once per outer element. The borrows end with the comprehension; an enclosing
loop's borrow of the same storage is merged, not replaced, and the scope's exit
puts back exactly the borrows held on entry.

A clause-0 source rooted at a name spelled like one of the targets (`[grow(c)
for c in c]`) is the ENCLOSING binding of that name, which the comprehension's
own code cannot reach by name, so no loan is filed on it.

A write through a loop variable reaches another comprehension's target (step
4 above) but not a parameter or an enclosing `for` variable, so `[c.bump(1) for
c in cs]` over a parameter keeps `cs` const and fails the build
(`BUGS.md#comprehension-loop-var-mutation-not-propagated`).

---

## Code Generation

THIR (`tpyc/thir/lower/comprehensions.py`) lowers a list/set/dict comprehension
to `THIRComprehensionBlock`: a statement expression around ordinary statements.

- One loop per clause, built by `build_loop_node` -- `THIRForRange` (a 1/2-arg
  range, or a 3-arg range whose step the `for` statement's step classifier
  admits), `THIRForEach` (begin/end over a container, a dict view, a combinator,
  a generator call held in `auto __obj_N`, a 3-arg `Range` object), or
  `THIRForIterProto` (a user iterable, an `Iterable[T]` / `Iterator[T]`:
  `::tpy::__iter__` + `__next__`). A tuple-unpacking clause opens its body with the `for`
  statement's `THIRTupleUnpack` head.
- Clause k+1's loop sits inside clause k's filters, so an inner source runs
  once per outer element that passed them.
- One nested `THIRIf` per filter, so a later filter runs only when the earlier
  ones passed (Python's short circuit).
- A `THIRCompInsert` leaf: `push_back` (list), `insert` (set),
  `insert_or_assign` (dict). Its operands' temporaries flush right above it,
  once per iteration that reaches it.

The block is a flush region of its own, except clause 0's source
(`source_in_enclosing` on the outermost loop): Python evaluates it in the
enclosing scope, so its temporaries are declared at the enclosing statement and
outlive the block -- a temporary a borrowed source lends from stays valid for a
pointer stored in an element and read afterwards. A filter walrus's
declaration also lands at the enclosing statement.

### Single clause with a filter

```python
pos = [x for x in data if x > 0]      # data: list[int32]
```

```cpp
std::vector<int32_t> pos = ({
    std::vector<int32_t> __result;
    auto& __obj_0 = data;
    __result.reserve(static_cast<std::size_t>(__obj_0.size()));
    auto __beg_0 = __obj_0.begin();
    auto __end_0 = __obj_0.end();
    for (; __beg_0 != __end_0; ++__beg_0) {
        int32_t x = *__beg_0;
        if ((x > 0)) {
            __result.push_back(x);
        }
    }
    std::move(__result);
});
```

Two filters nest: `[x for x in range(20) if x % 2 == 0 if x % 3 == 0]` renders
`if (... == 0) { if (... == 0) { __result.push_back(x); } }`.

### Several clauses

```python
kept = [x for row in grid if keep(row) for x in inner_src(row) if x > 1]
```

```cpp
std::vector<::tpy::BigInt> kept = ({
    std::vector<::tpy::BigInt> __result;
    auto& __obj_2 = grid;
    auto __beg_2 = __obj_2.begin();
    auto __end_2 = __obj_2.end();
    for (; __beg_2 != __end_2; ++__beg_2) {
        const auto& row = *__beg_2;
        if (::tpyapp::main::keep(row)) {
            auto __obj_3 = ::tpyapp::main::inner_src(row);
            auto __beg_3 = __obj_3.begin();
            auto __end_3 = __obj_3.end();
            for (; __beg_3 != __end_3; ++__beg_3) {
                const ::tpy::BigInt& x = *__beg_3;
                if ((x > 1)) {
                    __result.push_back(x);
                }
            }
        }
    }
    std::move(__result);
});
```

An inner range reads the outer variable like any other source:

```python
[i * 10 + j for i in range(4) for j in range(i)]
```

```cpp
for (int32_t i = 0; i < 4; ++i) {
    int32_t __stop_11 = i;
    for (int32_t j = 0; j < __stop_11; ++j) {
        __result.push_back((::tpy::add_check<int32_t>((::tpy::mul_check<int32_t>(i, 10)), j)));
    }
}
```

### Tuple unpacking

The loop variable binds the whole element and the targets read it, as in the
`for` statement. The holder draws its name from the for statement's own
per-module counter (`__for_tup_N`), so every clause gets a holder of its own
and no fixed spelling can hide a user name the element reads:

```python
{s: n * k for k in range(1, 3) for n, s in tagged if n >= k}   # tagged: list[tuple[int, str]]
```

```cpp
for (int32_t k = 1; k < 3; ++k) {
    auto& __obj_7 = tagged;
    auto __beg_7 = __obj_7.begin();
    auto __end_7 = __obj_7.end();
    for (; __beg_7 != __end_7; ++__beg_7) {
        const auto& __for_tup_1 = *__beg_7;
        const auto& __tup_1 = __for_tup_1;
        ::tpy::BigInt n = std::get<0>(__tup_1);
        std::string s = std::get<1>(__tup_1);
        if ((n >= k)) {
            __result.insert_or_assign(s, ((n) * (::tpy::BigInt(k))));
        }
    }
}
```

### Iteration strategies

| Iterable | Loop node | Render |
|----------|-----------|--------|
| `range(n)`, `range(a, b)` | `THIRForRange` | counter loop; a literal bound inlines, another is captured into `__stop_N` / `__start_N` |
| `range(a, b, step)` | `THIRForRange` stepped, else `THIRForEach` | `for (i = a; i > b; i += step)` with the overflow / zero-step checks the `for` statement emits; a step the classifier declines (too wide for the counter) iterates the `Range` object |
| `list[T]`, `Array[T, N]`, `Span[T]`, `str`, dict views, combinators, generator call | `THIRForEach` | `begin()/end()` over `auto& __obj_N` (lvalue) or `auto __obj_N` (rvalue) |
| user iterable, `Iterable[T]` / `Iterator[T]` | `THIRForIterProto` | `auto&& __itr_N = ::tpy::__iter__(__src_N);` stepping `__next__()` |

### Annotation propagation

When the user provides an explicit type annotation:

```python
result: list[int32] = [x for x in items]
```

The expected element type (`int32`) propagates into the element expression via
`analyze_expr_with_hint`, enabling coercions (int32 -> int, int32 -> int64), as
for list literals. Without an annotation the element type is inferred bottom-up.
Works in every hint-providing context: declarations, returns (`Own[list[T]]`),
arguments. An incompatible annotation is an error: "Type mismatch in list
comprehension element".

### Sized sources -> reserve

A single-clause LIST comprehension reserves its result to the trip count once
the source is captured. The loop node carries the result's name (`presize`),
because only the loop can spell its capture:

```cpp
auto& __obj_0 = data;
__result.reserve(static_cast<std::size_t>(__obj_0.size()));        // a Sized source

if (10 > 0) __result.reserve(static_cast<size_t>(10));             // range(10)
if (__stop_0 > 0) __result.reserve(static_cast<size_t>(__stop_0)); // range(n)
{ size_t __sz; if (__stop_0.to_size_checked(__sz)) __result.reserve(__sz); }  // BigInt bound
```

The reserve is a no-regret over-allocation under a filter. A 3-arg range, an
iterator-protocol source and a multi-clause comprehension (whose trip count is
not known up front) reserve nothing.

### Compile-time Array promotion

When a SINGLE-CLAUSE list comprehension's output size is known at compile time
and there are no filter conditions, the result type is `Array[T, N]` instead of
`list[T]`, producing `std::array<T, N>` with zero heap allocation.

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

It lowers to `THIRArrayComprehension`, not a loop: `tpy::array_from_index<T,
N>(f)` -- aggregate construction, the element evaluated exactly N times
left-to-right, each result constructed in place (no element default ctor or
assignment):
- `range(...)` with literal args: the per-index lambda binds the loop var as
  `start + i * step`
- `Array[T,N]` source: a statement-expression prelude borrows the source once,
  the lambda binds from `__obj[i]`; tuple-unpack shares the unpack head

---

## Dict Comprehension

Dict comprehensions share the clause model and produce `dict[K, V]` --
`TpyDictComprehension` (`key_expr`, `value_expr`, `generators`) and a
`THIRComprehensionBlock` of kind `dict` over `tpy::ordered_map<K, V>`, with
`insert_or_assign` for Python's overwrite semantics (a later key replaces the
value, the first insertion fixes the order). No `reserve()`.

When the value moves an owned loop variable, `insert_or_assign`'s two
arguments are unsequenced, so a key reading the element would read a
moved-from value; the key is evaluated into a local first (`key_first` on
`THIRCompInsert`):

```python
d = {w.id: w for w in widgets(3)}     # widgets() -> Iterator[Own[Widget]]
```

```cpp
for (; __beg_0 != __end_0; ++__beg_0) {
    auto&& w = *__beg_0;
    auto __dk_1 = w.id;
    __result.insert_or_assign(std::move(__dk_1), std::move(w));
}
```

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

Each further `for` clause is one more loop nested inside the previous clause's
filters, the `yield` innermost:

```python
total = sum(x + e for row in rows for x in row if x != 2 for e in extras)
```

```python
def __genexpr_f_2(__src, extras):
    for row in __src:
        for x in row:
            if x != 2:
                for e in extras:
                    yield x + e
```

Only clause 0's source is the frame's source param. An inner clause's source is
part of the body, evaluated once per outer element that passed the outer
filters, and the enclosing names it reads are captures like any other.

Sema analyzes the function ONCE, through the ordinary `_analyze_function`
lifecycle, under a function state of its own (the enclosing function's state is set aside and
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
  name the element, the filters and the inner clauses' sources read (`self` and a nested def's own captures
  included). The frame holds each in a deduced-type forwarding slot, which an
  lvalue argument makes a reference field of whatever C++ type the enclosing
  variable has -- so the storage form of that variable stays the enclosing
  body's business, and a rebind between two pulls is visible in the body.

Creating the frame hands the source and the captures over the way a call hands
over its arguments, and is checked as one, with the three checks every call
site runs, and one more for the inner clauses:

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
- **Inner clauses over a capture.** The loop of an inner clause that iterates
  a capture, or a field or element of one (`b_list`, `h.items`, `bs[0]`),
  records the iterated place (`TpyFunction.genexpr_inner_loans`, filed by
  `_register_foreach_iter_loans`), and the creation holds those loans with the
  source's (`frame_capture_loans`), so the consuming loop's growth of that
  place warns as growth of the source does. The frame iterates the capture
  through a reference to the enclosing VARIABLE, so a rebind of the variable
  between two pulls would leave the inner iteration walking freed storage:
  `reject_rebound_iterated_capture` refuses it -- *'b_list' is iterated by
  this generator expression, but the loop it feeds rebinds it between pulls;
  bind it to a local first* -- and the kept-genexpr scan below refuses it for
  a genexpr kept past its statement. CPython accepts both (its inner iterator
  keeps the old list alive; TODO.md "Lift: rebinding a capture a generator
  expression's inner clause iterates"). A kept genexpr's loans end with the
  statement that creates it, so GROWING its source or such a capture between
  pulls is not diagnosed (BUGS.md#kept-genexpr-source-grown-between-pulls).

```cpp
template <typename F_k>
struct __genexpr_f_1_frame : ::tpy::next_iter_mixin<__genexpr_f_1_frame<F_k>, int32_t> {
    ::tpy::frame_state __state;          // every frame carries it; unread in this form
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
| Stable lvalue container (name, field; a subscript is not admitted yet, BUGS.md#comp-subscript-element-source-rejects) | Its own type, by reference; walked by begin/end, so a reference element -- or a record unpacked from a tuple element -- aliases the source |
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

### Narrowing of a captured name (and rebinding an iterated one)

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
naming the fix (bind the narrowed value to a local first). The same scan
refuses a rebind of a capture an inner `for` clause iterates (*... but it is
kept past the statement and can be rebound between pulls; bind it to a local
first*), narrowed or not. The scan cannot see
types of statements it has not reached yet, so ANY statement that reads the
kept name and binds another counts as handing it on, a consuming one included
(BUGS.md#genexpr-kept-scan-consumer-binding).

### Emit shape

A single-clause genexpr frame has one resume point and it is the loop head, so
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

A genexpr with several `for` clauses has a loop head per clause, so it keeps
the `switch (__state)` dispatch. Its constructor still seeds the OUTERMOST
loop's begin/end pair over a borrowed container (`ctor_seeded_uid`), and the
first pull does not take that pair again; an inner clause's pair is taken in
the body, once per outer element.

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
| Async comprehension | `[x async for x in aiter]` | "Async comprehensions not yet supported" |
| Non-iterable source | `[x for x in 42]` | "Cannot iterate over type int32" |
| Incompatible annotation | `x: list[int32] = [s for s in strs]` | "Type mismatch in list comprehension element" |
| Name bound by two clauses | `[row for row in rows for row in row]` | "'row' is bound by two `for` clauses of this comprehension; rename one" |
| Read before the binding clause | `[x for row in rows if x > 0 for x in row]` | "'x' is read before the `for` clause that binds it" |
| Owned source of a multi-clause generator expression | `sum(p.v + k for p in widgets() for k in range(2))` | "a generator expression cannot iterate a source that yields owned values; use a list comprehension or a `for` loop" |
| Walrus binding a reference (CPython runs it) | `[c for c in xs if (last := c).n > 0]` | "a walrus inside a comprehension cannot bind 'last' to a reference to an object; use a `for` loop" |
| Genexpr inner-clause capture rebound | `for v in (a + b for a in xs for b in ys): ys = [v]` | "'ys' is iterated by this generator expression, but the loop it feeds rebinds it between pulls; bind it to a local first" |

---

## Test Plan

```
tests/cases/list/
    list_comp_basic/, list_comp_filter/, list_comp_unpack/, list_comp_annotation/
    list_comp_to_array/, comp_array_*/          # Array promotion
    comp_owned_elem_move/                       # Own[T] source moves the element
    comp_multi_for/                             # several clauses: every position,
                                                # source family, range step, filter
                                                # order, walrus, genexpr inner loans
    error_list_comp_annotation/, error_list_comp_not_iterable/
    error_list_comp_unpack_count/, error_list_comp_unpack_non_tuple/
    error_comp_multi_for_name_reuse/            # a name two clauses bind
    error_comp_multi_for_underscore_read/       # `_` two clauses bind, read
    error_comp_multi_for_read_before_bind/      # a filter reads a later clause's name
    error_comp_multi_for_read_before_bind_iter/ # an inner iterable does
    error_comp_walrus_reference/                # walrus binding an object

tests/cases/dict/
    dict_comp_basic/, dict_comp_filter/, dict_comp_unpack/, dict_comp_annotation/
    dict_comp_owned_move/
    error_dict_comp_not_iterable/, error_dict_comp_unpack_count/
    error_dict_comp_unpack_non_tuple/

tests/cases/set/
    set_comp_basic/, set_comp_filter/, set_comp_unpack/, set_comp_annotation/
    set_comp_owned_move/
    error_set_comp_not_iterable/, error_set_comp_unpack_count/
    error_set_comp_unpack_non_tuple/

tests/cases/iterators/
    genexpr_basic/, genexpr_frames/, genexpr_rvalue_sources/, genexpr_source_shapes/
    error_genexpr_multi_for_owned_source/       # owned source at clause 0
    error_genexpr_multi_for_capture_rebound/    # consuming loop rebinds an
                                                # inner-iterated capture
    error_genexpr_multi_for_retained_rebound/   # kept genexpr, same capture rebound
```
