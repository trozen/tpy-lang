# Iterator Overhaul - Migration Plan

Tracking doc for the refactor that collapses tpyc's dual-path iterator
handling into a single Python-iterator-centric pipeline, with C++
`begin()`/`end()` retained as a pure codegen optimization.

## Goal

- **Sema** only reasons about the Python iterator protocol
  (`__iter__`, `__next__`, `Iterator[T]`, `Iterable[T]`).
- **Codegen** decides the physical lowering: default universal path
  using `::tpy::__iter__` + `.__next__()`, with peephole optimizations
  for cases where C++ range-based-for is strictly better.
- `NativeIterable[T]` remains available as a marker protocol but sema
  has no special-case for it.

## Current state (snapshot)

Iteration is inspected/dispatched in four places with parallel
discrimination trees:

1. **Sema iterability queries** (`sema/list_literals.py::IterableHelper`)
   answer "is this iterable? what's the element type?" via five checks:
   `NativeIterable[T]` protocol, `extends NativeIterable`, `str`-types,
   `@error_return(StopIteration)` `__next__`, `__iter__()` method, and
   finally the TpyType-level `get_iteration_element_type()` fallback.
2. **Codegen for-loop dispatch** (`codegen_cpp/statements.py::_gen_for_each_loop`)
   has eight branches: enum, `OwnIter`/`CopyIter`, auto-consuming,
   protocol `Iterator`/`Iterable`, protocol `ReadOnlySpanLike`,
   `range()` peephole, `error_return __next__`, `__iter__()` method
   (native vs non-native), and a default begin/end fallback.
3. **Generator-body for-loop lowering** (`codegen_cpp/gen_generators.py`)
   mirrors dispatch 2 with its own branches (generator for-range,
   for-begin-end, for-next, for-iter-next, for-iter-begin-end).
4. **TpyType-level hook** `get_iteration_element_type()` overridden on
   `ListType`, `DictType`, `SetType`, `SpanType`, `ArrayType`,
   dict view types, `RangeType`, `GenExprType`, etc. -- duplicating
   information already in the `.py` stubs' `__iter__` declarations.

The C++ runtime (`runtime/cpp/include/tpy/dunder.hpp`,
`next_iter.hpp`) is already uniform: `::tpy::__iter__(x)` has overloads
for every built-in container plus a generic `std::ranges::input_range`
fallback, and `native_iterator` bridges begin/end to
`__next__() -> std::expected<T, StopIteration>`.

## Target state

**Sema** answers iterability with one rule: "does the type have
`__iter__(self) -> Iterator[T]` (either declared or auto-synthesized
from `__next__`)?"  The element type is T.

**Codegen** has a default universal lowering:

```cpp
auto __it_N = ::tpy::__iter__(<expr>);
for (;;) {
    auto __r_N = __it_N.__next__();
    if (!__r_N.has_value()) break;
    T x = ::tpy::unwrap_ref(*__r_N);
    // body
}
```

Plus explicit peepholes, in priority order:

1. `range(...)` literal call -> C-style counter loop (unchanged).
2. `OwnIter[T]` / `CopyIter[T]` iterables -> C++ range-based-for
   directly (these already expose begin/end; no adapter needed).
3. Concrete `NativeIterable` type (at codegen time) -> plain C++
   range-based-for, skipping the `native_iterator` wrapper. Preserves
   today's codegen for `list`, `dict`, `set`, `Span`, `Array`, `str`,
   `bytes`, etc.
4. `ReadOnlySpanLike[T]` protocol param -> `::tpy::as_span(x)` +
   range-for (kept as peephole for now; revisit later).

All other cases (user iterators, protocol-typed params, `__iter__`
returning a user type, error_return `__next__`, etc.) fall through to
the universal default.

## Design decisions made

- **(Q1 resolved)** `ReadOnlySpanLike[T]` stays as a codegen peephole
  (separate from iteration dispatch). Revisit / eliminate later.
- **(Q2 resolved)** `NativeIterable[T]` stays as a marker protocol.
  Sema does **not** special-case it. To make sema treat it uniformly
  as iterable, `NativeIterable[T]` will **extend `Iterable[T]`** in the
  `.py` stub (`lib/tpy/tpy/_core/_types.py`). This means every type
  satisfying `NativeIterable[T]` also satisfies `Iterable[T]` and has
  an `__iter__` (inherited). Codegen continues to detect `NativeIterable`
  (via `is_native_iterable`) for the range-for peephole.
- **(Q3 resolved)** The "concrete NativeIterable -> plain range-for"
  peephole is required in Phase 2 (not an afterthought), so codegen
  output stays byte-equivalent for built-in containers.
- **(Q4 resolved)** Full scope: for-loops + comprehensions + generator
  expressions + `list()`/`dict()`/`set()` constructors +
  `list.extend()`/`str.join()` + generator-function body lowering.
  Done in steps, not one shot.

## Open questions

- **Should `NativeIterable` eventually become codegen-only (non-user-facing)?**
  `docs/LANGUAGE_FEATURES.md` already says "should generally not appear
  in user-facing function signatures -- use `Iterable[T]` instead."
  Not in scope for this overhaul. Track as follow-up.
- **Is there any type where `get_iteration_element_type()` returns
  something different from `__iter__()`'s Iterator[T] parameter?**
  Audit during Phase 1. `DictType` returns K (matches
  `dict.__iter__() -> Iterator[K]`); views and other containers should
  line up too.

## Phased plan

### Phase 1 - Unify sema iterability queries -- DONE (2026-04-15)

**Scope:** sema only. No codegen changes, no snapshot changes expected.
Outcome: zero snapshot churn, full suite green (5346 passed / 930
skipped / 0 failed).

Changes actually made:
- `lib/tpy/tpy/_core/_types.py`: `NativeIterable[T]` now declares
  `__iter__(self) -> Iterator[T]`. **Option A was blocked**: the
  parser rejects "generic parent protocols" (e.g. `class Foo(Iterable[T], Protocol): ...`),
  so extending `Iterable[T]` directly was not viable. Option B (give
  `NativeIterable` its own `__iter__` method) achieves the same sema
  effect without touching the generic-parent-protocols limitation.
- `tpyc/sema/registration.py::register_protocol`: resolve protocol
  method signatures via `resolve_type(..., protocols_only=True)`
  during registration. **This uncovered a pre-existing latent bug**
  -- cross-module protocol references in protocol method signatures
  (e.g. `Iterator[T]` imported from `_typing` and used in `NativeIterable`'s
  stub in `_core/_types.py`) were stored unmarked (`is_protocol=False`,
  `_module_qname=None`), so later structural conformance checks failed
  on equality. Only surfaced now because this is the first cross-module
  protocol-protocol reference we've added. Fix applies to all protocols,
  not just iteration-related ones.
- `tpyc/sema/list_literals.py::IterableHelper` -- single dispatch
  tree:
  - protocol-typed iterables (`typing.Iterator`, `typing.Iterable`,
    `tpy.NativeIterable`, `tpy.ReadOnlySpanLike`)
  - compiler-internal iterator adapters (`CopyIterType`, `OwnIterType`,
    `GenExprType`, `SpanIterType`) -- these are sema constructs without
    records in the registry, so handled explicitly
  - `is_any_str_type` shortcut (unchanged)
  - `error_return __next__` (unchanged)
  - `__iter__()` lookup (now covers all built-in containers)
  - `get_iteration_element_type()` fallback **removed**.
- `tpyc/modules/type_resolution.py::get_iter_info`:
  - `allow_protocol_return=True` for the builtin-type path -- built-in
    `__iter__() -> Iterator[T]` stubs are now the single source of
    iteration-element truth.
  - Switched type-param extraction from `tpy_type.type_args` to
    `extract_type_params(tpy_type)` so specialized subclasses
    (`RangeType.elem`, `DictType.key_type/value_type`, etc.) contribute
    correctly.
  - Element-type substitution in `_find_iter_method_info` is now
    recursive (via `_resolve_type_or_param`), so compound `Iterator[tuple[K, V]]`
    returned by e.g. `dict_items.__iter__` substitutes both K and V.
- `tpyc/sema/statements.py` (for-loop iter-depth) -- treat a type as
  container-referencing when either `IterInfo.iter_is_native` is true
  OR `is_native_iterable(type)` is true. This preserves the old
  lifetime semantics for built-ins now that they flow through the
  unified `__iter__()` path.

Audit table (built-in `__iter__` stubs, verified present):

| Type | `__iter__` element | Source |
|---|---|---|
| `list[T]` | `Iterator[T]` | lib/tpy/tpy/_builtins/_list.py:22 |
| `dict[K,V]` | `Iterator[K]` | lib/tpy/tpy/_builtins/_dict.py:20 |
| `dict.keys()` | `Iterator[K]` | lib/tpy/tpy/_builtins/_dict.py:78 |
| `dict.values()` | `Iterator[V]` | lib/tpy/tpy/_builtins/_dict.py:39 |
| `dict.items()` | `Iterator[tuple[K,V]]` | lib/tpy/tpy/_builtins/_dict.py:58 |
| `set[T]` | `Iterator[T]` | lib/tpy/tpy/_builtins/_set.py:21 |
| `Range[T]` | `Iterator[T]` | lib/tpy/tpy/_builtins/_range.py:19 |
| `Span[T]` | `Iterator[T]` | lib/tpy/tpy/_core/_containers.py:24 |
| `Array[T,N]` | `Iterator[T]` | lib/tpy/tpy/_core/_containers.py:72 |
| `SpanIter[T]` | `Self` | lib/tpy/tpy/_core/_containers.py:138 |
| `str` | `Iterator[Char]` | lib/tpy/tpy/_core/_types.py:1585 |
| `bytes`/`bytearray` | `Iterator[UInt8]` | lib/tpy/tpy/_builtins/_bytes.py |
| `BytesView` | `Iterator[UInt8]` | lib/tpy/tpy/_core/_bytes_view.py:20 |

Tests to watch:
- All of `tests/cases/iterators/**`
- `tests/cases/calls/` (iterable-arg matching to `list()`/`dict()`/`set()`)
- `tests/cases/control_flow/` (for loops)

Phase 1 exit criterion: `uv run pytest -m "not slow"` passes without
snapshot changes. If any snapshot would shift, it's a sema logic bug in
this phase -- investigate.

Phase 1 follow-ups (not blockers, worth noting):
- Parser limitation "Generic parent protocols are not yet supported"
  (`_parse_protocol` in `tpyc/parse/parser.py`) blocks Option A above
  and is likely to come up again as protocols get composed. Tracked
  in `TODO.md` (root "Next" list).
- `SpanIter[T]` stays in `IterableHelper`'s compiler-internal adapter
  allowlist. Root cause: the parser resolves `__iter__(self) -> Self`
  to a plain `NamedType("SpanIter", (T,))` without the `SpanIterType`
  typesys subclass wrapper **and** without `_module_qname`, so
  neither the `isinstance(ret, SpanIterType)` branch nor the
  `qualified_name()` lookup match it inside `_find_iter_method_info`.
  Two paths forward: (a) parser-side resolution of `Self` to the
  specialized typesys subclass (requires knowing that "SpanIter" maps
  to `SpanIterType`); (b) a sema `resolve_type` pass that converts
  known-builtin `NamedType` instances to their typesys subclasses;
  (c) change SpanIter's stub to `__iter__(self) -> Iterator[T]`
  (smallest diff, loses the "I am my own iterator" idiom in the stub).
  Phase 1 still made progress: `SelfType` returns are now substituted
  in `_find_iter_method_info`, which helps user iterator records that
  declare `__iter__(self) -> Self` and whose parser did retain the
  `SelfType` marker. Low priority while the adapter list is tiny.
- Diagnostic quality for protocol conformance failures is now
  richer (shows the expected vs actual method signatures). If the
  signature-mismatch case needs further categorization later (e.g.
  wrong mutation, wrong type-param defaults), expand
  `_describe_method_mismatch` in `tpyc/sema/protocols.py`.

### Phase 2 - Codegen peepholes + universal default -- DONE (2026-04-15)

**Scope:** `codegen_cpp/statements.py::_gen_for_each_loop`, plus a
minor runtime/stub fix to `SpanIter`. Snapshot churn landed for
protocol-typed iterables, user `__iter__()` methods, error_return
`__next__` iterators, and narrowed-union for-loops that previously
fell through the default begin/end fallback. Built-in containers
stayed byte-equivalent (the NativeIterable peephole protects them).

Design: one universal default for everything non-peephole.

Final branch order in `_gen_for_each_loop`:
1. Enum iteration (begin/end over `EnumUtil::members`)
2. `OwnIter`/`CopyIter` (begin/end)
3. Auto-consuming (`consuming_iter_fi`)
4. `ReadOnlySpanLike` protocol -> `as_span` + begin/end
5. `range()` counter loop (or `Range<T>` begin/end fallback)
6. Concrete `NativeIterable` (built-in only, or `NativeIterable[T]`
   protocol param) -> plain C++ begin/end range-for. User records
   do not derive NativeIterable (Phase 3 removed auto-derivation)
   and always fall through to the universal default.
7. Universal default: `auto&& __itr = ::tpy::__iter__(src);
   for (;;) __itr.__next__()`. Handles user NativeIterables safely
   via `auto&&` lifetime-extension of the returned iterator value.

Universal default handles: protocol `Iterator[T]` and `Iterable[T]`,
`error_return __next__` iterators, user `__iter__()` methods (both
native-iter-returning and separate-iterator-returning variants),
move-only owning iterators (map/filter/zip/enumerate/reversed results),
and user iterator records iterated in-place.

Two implementation details are load-bearing for the single-path design:

- **`auto&&` binding** for `__itr`. Universal ref deduces `T&` for
  reference returns (iterator self& -- in-place consumption, no copy,
  works with move-only owning iterators) and lifetime-extends value
  returns (container -> native_iterator fallback).
- **`SpanIter.__iter__` is non-const.** The previous `@readonly` /
  `const SpanIter& __iter__() const` was a design wart: Python iterators
  need `__next__` to mutate, so a const SpanIter was unusable anyway.
  Removing `@readonly` from the stub + changing the runtime to return
  `SpanIter&` makes `auto&&` deduce `SpanIter&`, keeping the single path
  correct. No test relied on const-iterable SpanIter.

Consumption semantics verification:
`for_iterator_{field,method,subscript,}_consumption` tests exercise
"iterate twice, second iteration should see exhausted iterator."
With `auto&&` the iterator binds by reference and is mutated in place,
so the tests pass.

Move-only verification:
`builtins/{map,filter,zip,enumerate,reversed}_rvalue` exercise RVO-only
owning iterators. With `auto&&` the iterator binding is a reference
to the RVO'd source, so no copy/move is required after construction.

Tests: `uv run pytest --force-exec` green (2503 passed, 1 skipped).

Phase 2 follow-ups (not blockers):
- `IterInfo.iter_is_native` is no longer referenced in the for-loop
  dispatch; audit remaining callers (list comprehensions, generator
  expressions, `in` operator) during Phase 3.
- Pre-existing bug surfaced during Phase 2 audit (not caused by this
  phase): early-return isinstance narrowing on non-recursive unions
  misses the `std::get<X>(var)` binding emission; subsequent accesses
  fail to compile. Tracked in TODO.md (Bugs section).

### Phase 3 - Eliminate `get_iteration_element_type()` -- DONE (2026-04-16)

**Scope:** replaced all `get_iteration_element_type()` callers with
the new unified `get_iterable_element_type(type, registry)` accessor
in `modules/type_resolution.py`. Removed the `get_iteration_element_type`
method from `TpyType` base class and all 10 subclass overrides.
Fixed `in` operator fallback for non-builtin types. Two snapshot
changes (user NativeIterable `in` + NativeIterable[T] protocol `in`).

Changes:
- `tpyc/modules/type_resolution.py`: new standalone
  `get_iterable_element_type(tpy_type, registry)` function -- single
  source of truth for iteration element types. Covers all categories:
  compiler-internal adapters (CopyIter, OwnIter, GenExpr, SpanIter),
  iterable protocol params, str types, error_return __next__, and
  __iter__() methods.
- `tpyc/modules/__init__.py`: export `get_iterable_element_type`.
- `tpyc/sema/list_literals.py`: `IterableHelper.get_iterable_element_type_or_none`
  now delegates to the standalone function.
- `tpyc/sema/calls.py`: 4 sites replaced (copy_iter, own_iter,
  protocol param hint, generic constructor validation). The protocol
  param hint in `_concrete_hint_from_param` (a @staticmethod without
  registry access) inlines the iterable-protocol type_arg extraction
  directly.
- `tpyc/sema/statements.py`: 1 site replaced (consuming iter
  value-type check).
- `tpyc/codegen_cpp/expressions.py`: 6 sites replaced (copy_iter
  elem, array comp range/non-range, comprehension IIFE, generator
  expr, comp scope var registration).
- `tpyc/codegen_cpp/gen_generators.py`: 1 site replaced. Changed
  from `get_iteration_element_type()` (broad match) to builtin
  NativeIterable check (`record.is_native` + `is_native_iterable`)
  to avoid incorrectly routing user records through begin/end.
- `tpyc/codegen_cpp/expressions.py` (`in` operator): non-builtin
  types now use `::tpy::__iter__` + `.__next__()` statement expression
  with `::tpy::unwrap_ref` for val_or_ref compatibility. Built-in
  NativeIterables and `NativeIterable[T]` protocol params use
  `std::ranges::contains` (C++23, single-evaluation).
- `tpyc/typesys.py`: removed `get_iteration_element_type` from
  base `TpyType` and 10 subclass overrides (CopyIterType, OwnIterType,
  DictType, SetType, DictKeysViewType, DictValuesViewType,
  DictItemsViewType, GenExprType, PendingDictType, PendingSetType).

User records no longer auto-derive NativeIterable (removed from
`tpyc/sema/registration.py`). NativeIterable is a marker for native
(built-in) types only; user records keep synthesized begin/end for
C++ interop but are not recognized as NativeIterable by the compiler.
This eliminated the `_is_user_native_iterable` workaround branches
in comprehension and genexpr codegen, and simplified the for-loop
NativeIterable peephole check (no more `is_user_native` exclusion).

**Items resolved in Phase 4:**
- `IterInfo.iter_is_native` no longer used by gen_generators
  (`iter_begin_end` strategy eliminated).

### Phase 4 - Unify generator-function for-loop lowering -- DONE (2026-04-16)

**Scope:** `codegen_cpp/gen_generators.py` (strategy analysis + simple
generator codegen) and `codegen_cpp/statements.py` (complex generator
emit). Generator-specific machinery (yield-point splitting, state
saving) unchanged.

**Simple generator** (lambda path) restructured from 3 branches to 4:
1. Range -> counter (unchanged)
2. Iterator[T] direct -> `__next__()` without `__iter__()` (avoids
   copying move-only iterators)
3. Built-in NativeIterable -> begin/end peephole
4. Universal default -> `::tpy::__iter__()` + `__next__()` (handles
   Iterable[T], NativeIterable[T] protocol params, user types,
   error_return __next__)

**Complex generator** (state machine path): eliminated `"iter_begin_end"`
strategy. All non-range, non-builtin-NI, non-direct-iterator types now
use `"iter_next"` strategy with `::tpy::__iter__()`. Struct field types
use `decltype(::tpy::__iter__(...))` for correct deduction.

Two snapshot changes: `gen_for_user_iter` (struct field types) and
`gen_generic_own_iterable` (simple generator path). Both compile and
execute correctly.

### Phase 5 - Revisit optional simplifications

Non-blocking cleanups that can happen independently:

- `ReadOnlySpanLike` peephole: either fold `__span__`-only types into
  Iterable by synthesizing `__iter__` as
  `self.__span__().__iter__()`, or keep as-is.
- Phase out `NativeIterable[T]` as a user-facing parameter type in
  favor of `Iterable[T]` + codegen specialization (see
  `LANGUAGE_FEATURES.md`).
- `IterInfo.iter_is_native` is no longer used in codegen (gen_generators
  eliminated it in Phase 4). Remaining caller: sema iter-depth lifetime
  tracking in `statements.py`. May collapse entirely.

## File-by-file impact matrix

| File | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| `lib/tpy/tpy/_core/_types.py` | NativeIterable gets `__iter__` [DONE] | - | - | - |
| `tpyc/sema/registration.py` | resolve protocol method sigs [DONE, bonus] | - | - | - |
| `tpyc/sema/list_literals.py` | simplify [DONE] | - | delegates to standalone fn [DONE] | - |
| `tpyc/sema/expressions.py` | - | - | (no change needed) | - |
| `tpyc/sema/statements.py` | iter-depth uses is_native_iterable [DONE] | - | use unified accessor [DONE] | - |
| `tpyc/sema/calls.py` | - | - | use unified accessor [DONE] | - |
| `tpyc/modules/type_resolution.py` | `allow_protocol_return`, `extract_type_params`, recursive substitution [DONE] | - | add `get_iterable_element_type` [DONE] | - |
| `tpyc/codegen_cpp/statements.py` | - | peepholes + universal default [DONE] | (no change needed) | - |
| `tpyc/codegen_cpp/expressions.py` | - | UB containment (comps, `in`) [DONE] | use unified accessor + `in` fallback -> `__iter__`+`__next__` [DONE] | - |
| `tpyc/codegen_cpp/gen_generators.py` | - | - | use unified accessor + builtin NI check [DONE] | collapse to 4 strategies [DONE] |
| `lib/tpy/tpy/_core/_containers.py` | - | SpanIter `@readonly` removed [DONE] | - | - |
| `runtime/cpp/include/tpy/span_iter.hpp` | - | non-const `__iter__` [DONE] | - | - |
| `tpyc/typesys.py` | - | - | removed `get_iteration_element_type` (base + 10 overrides) [DONE] | - |
| `docs/ITERATOR_DESIGN.md` | update status table | update architecture | - | - |

## Testing checklist per phase

For each phase, run in order:
1. `uv run pytest -m "not slow"` (fast compilation snapshots)
2. `uv run pytest tests/test_exec.py -k iterators` (iterator runtime)
3. `uv run pytest` (full suite + CPython compat)
4. Spot-check generated C++ for:
   - `for i in range(10): ...` (counter-loop peephole)
   - `for x in a_list: ...` (concrete NativeIterable peephole)
   - `for x in a_dict: ...` (dict keys)
   - `for c in a_str: ...` (str chars)
   - `for x in user_iter: ...` (universal path via `__next__`)
   - `for x in a_native_iterable_param: ...` (universal path; protocol)
   - `for x in gen_func(): ...` (generator result)

## Progress log

- **2026-04-15**: plan drafted on branch `final-init-binops`.
- **2026-04-15**: Phase 1 complete on branch `iterator-overhaul`. Full
  suite green (5346 passed / 930 skipped / 0 failed), zero snapshot
  changes. Bonus finding: pre-existing latent bug in
  `register_protocol` where cross-module protocol references in
  protocol method signatures were not flag-resolved -- fixed.
- **2026-04-15**: Phase 2 complete on branch `iterator-overhaul-dev` (from
  `iterator-phase1`). Full suite green (2502 passed / 1 skipped) both
  with snapshot compare and `--force-exec`. Built-in containers
  stayed byte-equivalent; initial two-path design (kept is-iterator
  peephole) was simplified to a single universal default after
  fixing the `auto` -> `auto&&` binding and SpanIter's const __iter__
  wart. Final shape is the original plan: peepholes plus one
  universal default.
- **2026-04-15**: Post-review UB fix. Reviewer flagged that the initial
  NativeIterable peephole called begin()/end() directly on user records
  (MutBuffer/Stack/ArrayList), whose begin/end are codegen-synthesized
  as `this->__iter__().begin()` on a temporary iterator. Currently
  safe for SpanIter (returns raw span iterator, no self-pointer), but
  fragile against future iterator types with back-pointers. Fix: exclude user records (via
  `record.is_native == False`) from the NativeIterable peephole; they
  fall through to the universal default which safely lifetime-extends
  the iterator value via `auto&&`.
- **2026-04-15**: In-session cleanups (same branch):
  - Diagnostic: `get_missing_protocol_methods` replaced by
    `get_protocol_conformance_issues` in `tpyc/sema/protocols.py` --
    now distinguishes "missing method X" from "method X has wrong
    signature: expected ... got ...". Three pre-existing error-snapshot
    test diag.txt files updated accordingly, along with their inline
    `# tpyc: error(/.../)` annotations.
  - Partial SpanIter unification: `_find_iter_method_info` now
    substitutes `SelfType` returns via a `Self` entry threaded through
    `type_subst` from `get_iter_info`. Helps user iterators with
    `__iter__(self) -> Self`. SpanIter itself stays in the adapter
    allowlist (see follow-up note); full unification needs a
    parser/sema change that's out of scope for Phase 1.
- **2026-04-16**: Phase 3 complete on branch `iterator-phase1`. Full
  suite green (2507 passed / 1 skipped). Three intentional snapshot
  changes: `arraylist_membership` (comprehension + `in` operator),
  `for_native_iterable` (NativeIterable[T] protocol `in` operator).
  `get_iteration_element_type` removed from TpyType and all subclasses;
  new standalone `get_iterable_element_type` in `modules/type_resolution.py`
  is the single source of truth. User records no longer auto-derive
  NativeIterable; `_is_user_native_iterable` workaround eliminated.
- **2026-04-16**: Phase 4 complete on branch `iterator-overhaul-dev`.
  Full suite green (2524 passed / 1 skipped). Two snapshot changes:
  `gen_for_user_iter` (complex generator struct field types now use
  decltype), `gen_generic_own_iterable` (simple generator uses
  universal __iter__+__next__ instead of begin/end). Eliminated
  `iter_begin_end` strategy and `_gen_generator_for_iter_begin_end`.
  `IterInfo.iter_is_native` no longer used by gen_generators.

## Out of scope

- `__reversed__` / `reversed()` user types (roadmap item, independent)
- `next()` / `iter()` builtins design changes (stable)
- `yield from`, generator `send()`/`throw()`/`close()` (separate roadmap)
- `__contains__` / `in` user-type dispatch design (separate roadmap).
  The `in` fallback (no `__contains__`) now uses `__iter__`+`__next__`
  for non-builtin types (Phase 3). Built-in NativeIterables and
  `NativeIterable[T]` protocol params use `std::ranges::contains`.
- Iterator combinators (`enumerate`, `zip`, `map`, `filter`) design --
  already working
- Async iterators
