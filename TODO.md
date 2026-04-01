# TODO

See docs/FEATURE_ROADMAP.md for bigger tasks

## Next
- Emit `this->` for self method calls: codegen currently emits bare `method(args)` for `self.method()` calls (`expressions.py:1994`). Prefer explicit `this->method(args)` for readability.
- Combinator composition: reference preservation lost when chaining (e.g. `enumerate(map(identity, pts))` yields copies, not refs). The `owning_enumerate_iter` copies elements into `std::tuple<int32_t, T>` instead of using `val_or_ref<T>`. Need runtime support for reference-preserving tuples in composed iterators.
- `zip(map(...), map(...))` fails: `owning_zip_iter` stores iterators via `decltype(__iter__(...))` which gives `map_iter&` (self-reference) for `__next__`-based types. The `iters_` tuple stores dangling references to the `make_iters` return value. Fix: store iterators by value (`std::remove_reference_t`) or add a `zip_iter` path for `__next__`-based rvalue inputs.
- Ref[T] follow-up: warnings for implicit Ref->owned materialization (Phase 6 from the Ref design). Warn when Ref[T] is silently converted to owned T in storage positions where the copy is surprising. Suppressible via `copy()` or `Own[...]`.
- Ref[T] follow-up: cleanup (Phase 7). Remove `is_value_type()` branching from base class `to_cpp_return()`/`to_cpp_param_type()`. Remove TypeParamRef's to_cpp overrides (now handled by `Ref[TypeParamRef]`). Replace remaining `to_cpp_*` method dispatch with centralized lowering API.
- Resolve class-level type params in cpp_template at sema time: when sema resolves a generic constructor like `list[Int32](range(10))`, substitute `{T}` -> `Int32` into the template and store a fully-resolved `cpp_template` on `resolved_function_info`. Codegen would then never see unresolved type params -- every template would only have `{0}`, `{1}`, `{cpp}`. Eliminates the `type_subst`/`extract_type_params` machinery in codegen's call_type block and the regex guard in `_gen_call`.
- Eliminate concrete type classes (ListType, DictType, etc.): replace `isinstance(t, ListType)` checks with name-based or annotation-driven checks. ~60 references for ListType alone across type inference, codegen, and compatibility. Enables treating all types uniformly as NamedType + RecordInfo. Lower priority -- current type classes work fine, this is about uniformity.
- Ptr null-provenance warning: consider warning when accessing through a Ptr with unknown provenance (similar to Optional access warnings). Design question: warn on all unknown-provenance access (noisy for function params) vs only when provenance is lost (was non-null, then reassigned from unknown source)?
- drop builtin types, like RangeType
- Move type resolution from parser to sema: parser still creates canonical types (FixedIntType etc.) via `_resolve_primitive_type` for `from tpy import` names. Future: sema resolution pass so parser only creates NamedType and sema resolves via @builtin_type factories. Blocked on types appearing everywhere in AST (expressions, constructor calls, etc.) -- needs comprehensive AST walker or lazy normalization.
- Define decorators as .py functions: readonly/noalloc/nocopy/pure/dynamic/error_return (tpy), native/cpp_template/builtin_type (tpy.extern), overload/override (typing) are currently parser keywords with empty stub files. Want them as real function definitions eventually so the parser doesn't need special handling. Blocked on: functions need type annotations to compile, and decorator signatures have no meaningful type (identity function over any callable).
- ContextManager[T] protocol


## Bugs
- Generic generators with multiple yield points (struct-based codegen path) are not yet supported -- the out-of-line `__next__()` in .cpp won't link for template structs. Currently guarded with a sema error. Fix: emit struct + `__next__()` body into the header when the function has type params.
- Non-native functions in builtin modules can't be called from user code: codegen emits unqualified names (e.g. bare `enumerate(...)` instead of `tpystd::builtins::enumerate(...)`). The `imported_names` path considers any registered function as "shadowing" the import. Blocks defining pure TPy generator builtins. Workaround: use `@cpp_template`/`@native` with C++ implementation instead.
- Generator codegen emits `T&` instead of `T` in yield tuple type when a generic generator has an `Own[Protocol[T]]` parameter. E.g. `def foo[T](items: Own[Iterable[T]]) -> Iterator[tuple[Int32, T]]` generates `std::tuple<int32_t, T&>` instead of `std::tuple<int32_t, T>`, causing C++ compilation failure. The Own wrapper on a protocol param confuses the type substitution for the yield type.
- Use-after-free not caught by borrow checker: `old = d.get(k); del d[k]; use(old)` -- `dict.get()` returns a pointer into the dict's internal storage. `del d[k]` frees that entry, making `old` a dangling pointer. The subsequent `use(old)` is undefined behavior (reads garbage). The borrow checker should warn that `old` borrows from `d` and `del d[k]` invalidates it. Workaround: reorder to use `old` before `del`.
- Overload resolution: generic overloads with protocol params lose to concrete overloads with coercion. `sum[T: AnyFixedInt](Iterable[T])` doesn't match `list[Int32]` in pass 1 because `_structural_match` requires `type(arg) == type(param)` -- `ArrayType` != `NamedType("Iterable")`. The concrete `sum(Iterable[float])` then wins via coercion in pass 2. Fix: `_structural_match` should check protocol conformance when `param` is a protocol type, so generic overloads with protocol params match in pass 1. Workaround: add concrete overloads for common types (Int32, Int64) before the generic.

## Macros
- all macro code (at least for json model) generated in headers
- json: pretty printing
- json: to/from file
- json: better error message: show position
- json: model inheritance
- json: field renaming
- Macro API: companion type creation: macros can add methods but not new types. `cls.add_companion_enum(name, members)` would let macros generate helper enums (e.g. key enums for JSON field dispatch via `try_parse` + `match`/`case`). Combined with string match or used standalone, this gives O(1) key dispatch.


## Bytes
- `BytesView` as dict key / set element: `__hash__` works but `std::span<const uint8_t>` has no `operator==`, so `ordered_map`/`ordered_set` fail to compile. Needs an `__eq__` overload or `std::equal_to` specialization.

## Ownership & Consuming Iteration
- Consuming `items()` for dict: consuming `__iter__` works for keys, but consuming `items()` can't use `@overload` with `Own[Self]` because the return type changes (`dict_items` -> `Iterator[tuple]`), breaking non-iteration callers like `len(d.items())`. Needs a mechanism that only selects consuming dispatch when used as a for-loop iterable.
- `own_iter()` is list-only: kept as explicit escape hatch for non-last-use consuming. Could be made generic (dispatch to consuming `__iter__` on any type).
- `own()` builtin: explicit `T -> Own[T]` conversion (analogous to `span()` -> `Span[T]`).
- User-defined drain iterators (e.g. `ArrayListDrainIter`): view types with borrow tracking. See `docs/CONSUMING_ITERATION_DESIGN.md`.

## Safety
- Reassignment of borrowed variable doesn't warn: `remove_storage_borrows` silently clears active borrows on reassignment (`s = "other"`) without warning. Only mutation (`.append()`, `del`) triggers warnings. Affects all types but especially `str` in generators -- the only way to invalidate a `string_view` is reassignment/destruction, which is exactly the case the tracker misses.
- Globals not tracked in borrow system: `mark_param_mutated` only checks `current_param_names` (`context.py:688`), so global variable mutations are invisible to the borrow tracker. `ref = g[0]; g.append(x)` produces no warning.
- Nonlocal mutations invisible to outer scope: nested function analysis runs in isolated state (`save_function_state`/`reset_function_tracking`). Mutations to nonlocal variables inside closures don't propagate back to the outer function's borrow tracker.
- User-type indirect structural mutation not detected: `_is_invalidating_method` uses `direct_structural_mutated_params` (Phase 1), so `add_twice()` calling `self.add()` which does `self._items.append()` is not caught. Phase 2 propagates `structural_mutated_params` transitively, but the borrow check runs during Phase 1. Fix: add deferred borrow checks after Phase 2, similar to `resolve_pending_borrow_checks`.
- `Span[str]` subscript view: `SpanType.subscript_borrows()` is intentionally not overridden because `v = s[0]` registers `s` as the str-borrow source, but mutations to the backing container (`arr[0] = "x"` where `s = Span[str](arr)`) call `mark_str_borrowers_mutated("arr")` -- missing `s`. Fix requires `mark_str_borrowers_mutated` to chase the borrow tracker's alias chain so backing-container mutations also invalidate views borrowed through spans.
- View type borrow tracking for user types: currently only built-in view types (Span, Ptr) are tracked as borrows. Likely needed when designing tpy stdlib types. See escape analysis design doc (Future Extensions) for field-level vs class-level annotation tradeoffs.
- Hoisted non-value loop variable as view: `for s in items: ... print(s)` where `s` is `std::string_view` -- the hoisted `s` is a view into the container, not a copy. If the container is mutated between loop exit and use of `s`, the view dangles. Same concern exists with `const auto&` loop vars. Consider emitting a value copy for hoisted non-value loop variables.

## Build pipeline
- when a module forwards imports only, those imports should not generate headers if the symbols are not used
- do not compile .cpp files that are not needed, e.g. no function is called etc

## Investigate
- CPython native (C) module for tpy stubs: the `lib/cpy/tpy/` stubs are pure Python. A C extension module could improve CPython performance for programs that use tpy types (Int32, Array, Span, etc.) heavily.
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- zig language: what it is, how is it different from C, what useful patterns can we learn
- Distinct types (newtypes): `class Meters(Distinct[float]): pass` -- zero-cost wrapper that creates a nominally distinct type. Prevents mixing incompatible values of the same underlying type (units, IDs, currencies). C++ codegen: thin struct wrapper or strong typedef. Inspired by Nim's `distinct` keyword.
- `@constexpr` decorator: mark pure functions as compile-time evaluable, emit C++ `constexpr`. Useful for lookup tables, constants, config. Low implementation cost -- lean on C++ compiler. `TABLE: Final[Int32] = factorial(10)` computed at compile time.
- Inline iterators: `@inline` on generator functions to unroll the loop body at the call site instead of creating a state machine. Zero overhead, important for hot paths. Generalizes what `range()` already does implicitly to user-defined iterators. Inspired by Nim's inline vs closure iterator distinction.

## Builtins
- `sorted(key=)`, `min(key=)`, `max(key=)`: accept an optional `key` parameter (`Fn` or `Callable`). `sorted(items, key=lambda x: x.score)` is extremely common. The lambda/Fn infrastructure is already there -- just needs builtin signatures and codegen for comparison-via-key.
- `map()` / `filter()`: reference preservation through single-combinator usage works via `Ref[T]`. Composed combinators (e.g. `enumerate(map(...))`) lose references -- see "Combinator composition" in Next section.
- `open()` binary mode: needs string literal overload dispatch so `open(path, "rb")` returns `BinaryIO` while `open(path, "r")` returns `TextIO`. Requires compiler support for overload resolution based on literal argument values.
- type(); (in future `T = type(x); z = T()`)
- tpy.ctypes.CInt32
- ptr() function? Auto-select Ptr vs Ptr[readonly[T]] based on binding mutability. Needs sema-level magic (mutability not in type, it's in binding context).
- `tpy.unsafe.unsafe_address_of(x) -> int`: return the memory address of an object as an integer. Useful for identity comparison in tests (proving reference semantics vs silent copy). C++ codegen: `reinterpret_cast<uintptr_t>(&x)`.

## Python features
- Any
- dynamic attributes
- list/container slicing (Phase 3: step)
- properties
- Generator: `yield from`, `send()`, `throw()`, `close()`
- Generator: protocol-typed params (`def gen(it: Iterator[T])`) -- needs template struct + factory. Currently emits `T&` without `template<typename T>` and simple generator path tries `begin()/end()` instead of `__next__()`.
- Generator: liveness optimization -- only promote yield-crossing variables to struct fields, keep others as stack locals in `__next__()` (currently all locals are promoted)
- Closures: `std::move_only_function` for `Callable` with `Own[T]` params/returns (design doc says to use it, currently uses `std::function` with rvalue params which works but is incorrect per C++23 semantics)
- Closures: `@noalloc` enforcement (Phase 5) -- `Callable` in `@noalloc` context should be rejected. Currently compiles without error.
- Closures: method references (`obj.method` as a value) -- needs partial application binding `self`. Currently gives "has no field" error.
- Protocol isinstance in ternary expressions: `x = a.foo() if isinstance(a, P1) else a.bar()` generates a runtime `?:` but both branches must be valid C++ at template instantiation time. Fix: generate an IIFE with `if constexpr` inside, e.g. `[&]() -> T { if constexpr (P1<T_a>) { return a.foo(); } else { return a.bar(); } }()`. This also enables single-line field init in `__init__` (goes into the C++ member initializer list instead of requiring unconditional pre-assignment + reassignment in branches).
- Allow `@runtime_checkable` decorator on protocols (no-op in tpyc, enables CPython compatibility for isinstance checks on user-defined protocols)
- `del x` (variable unbinding): complex in compiled context -- needs lifetime/scope analysis. Low priority.
- allow type annotation to use "" (forward decl)
- C-style for loop: reassigning loop variable affects iteration (differs from Python)

## Documentation
- language restriction documentation

## Refactor
- Own[T] variable types cleanup: `rvalue_vars`, `consumed_loop_vars`, `move_through_vars`, and `local_var_is_movable()` are now redundant for Own[T] variables (movability is in the type). These side channels are still used by flow_facts, init_tracker, and scope_tracker for branch merging. Remove incrementally: replace `_is_movable_var` with scope type check, then simplify flow_facts save/restore, then remove `rvalue_vars`.
- Inplace operator C++ return type: `__iadd__`/`__ior__` etc. declare returning self type (Python semantics) but the C++ helpers (`list_extend`, `dict_update`) return `void`. Add thin wrappers that return `T&` (e.g. `list_extend_inplace`, `dict_update_inplace`) so the declared return type matches the C++ signature. Affects list and dict.
- `tpy::ptr_variant<Ts...>` wrapper: replace `std::variant<Ts*...>` in param signatures with a thin wrapper that has an implicit converting constructor from `ptr_variant<T*...>` to `ptr_variant<const T*...>`. This would let deduced-const (non-mutated) params use deep const consistently with `@readonly`, without breaking callers. Currently deduced-const uses shallow const (`const variant<T*...>`) which doesn't prevent mutation through the pointer at the C++ level. Start with param signatures only; locals/fields/returns can stay `std::variant`.
- Inline `and`/`or` chains for side-effect-free operands: `a or b or c` currently emits an intermediate temp (`auto&& __tmp = (a ? a : b); result = (__tmp ? __tmp : c)`) to avoid double-evaluation. When all operands are provably side-effect-free (variables, literals), the temp is unnecessary and the chain can be emitted as a single nested ternary (`!a.empty() ? a : !b.empty() ? b : c`), which is more readable.
- `tpy::tpy_callable<F, R(Args...)>` concept: replace verbose `requires requires(__F0& __fn, ...) { { __fn(...) } -> std::convertible_to<R>; }` blocks with a compact `tpy::tpy_callable<__F0, R(Args...)>` concept in the runtime. Same `R(Args...)` notation as `std::function`. Needs a helper trait with partial specialization (`std::invocable` + `std::convertible_to` for return, void special case). Cosmetic improvement -- only worth prioritizing if it measurably speeds up C++ compilation for template-heavy Fn usage.
- Unify rvalue materialization: two overlapping mechanisms exist for extending rvalue lifetimes -- the slot system (`std::optional<T>` slots in `_gen_pointer_local_init`, tied to var decl infrastructure) and TempState (`auto&&` temps, expression-level). Both solve the same problem at different abstraction levels. Consider exposing a lower-level "materialize this rvalue" API that both var decls and expression-level temps (e.g. `and`/`or` ternaries) can share.
- `slice` type resolution: `slice` is special-cased in `_resolve_primitive_type` (parser.py) alongside `int`/`float`/`bool`/`str` because the fallback path in `_resolve_registered_type` uses `name[0].isupper()` to optimistically create NamedType for unresolved names. Lowercase builtin types like `slice` don't pass this heuristic, even though `is_known_type("slice")` returns True (the builtins module registers it). The deeper issue is that `_resolve_registered_type` returns `NamedType("slice")` instead of `SliceType`, causing type identity mismatches in union isinstance checks. Builtin types should resolve through the same path as user types instead of needing parser special cases.

## Random items
- AddressSanitizer test mode: add `--asan` flag to compile exec tests with `-fsanitize=address` to detect memory leaks, double-free, and use-after-free. Important before serious usage of `__copy__` + `__del__` patterns (e.g. CopyableBox[T]).
- Large value-type copy warning: estimate record sizes from fields and warn when Own[T] passes a large type by value (e.g. >128 bytes). For inline data there's no cheap move -- the bits are the object. Suggest Own[Box[T]] for cheap ownership transfer (moves a pointer) or Ptr[T]/Ptr[readonly[T]] for borrowing. Stricter threshold in @noalloc contexts.
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- ultimate goal: make tpyc compile with tpyc
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)
- Fixed-int division semantics: Python `//` uses floor division (round toward -inf), C++ `/` uses truncation (round toward zero). They differ for negative operands (`-7 // 2` = `-4` in Python, `-3` in C++). Current codegen emits `div_floor`/`mod_floor` which adds a branch per operation. Options: (A) configurable flag like `--int-div=floor|trunc` (similar to `--default-int`), (B) default to truncation for fixed-width types (users opting into Int32 already accept C++ semantics), BigInt always floor, (C) rely on range tracking to emit raw `/` when both operands are provably non-negative (no semantic change, but only helps when compiler can prove it)
- c++ generation profiles: utf8 strings vs char strings
- static_cast<char> -- should rather use checked cast (policy based)
- `Own[T]` for argument passing: callee takes ownership (how to pass an object from pointer? require explicit copy?)
- `DefaultInt` type alias: currently a sentinel class in `tpy.extern` used only by `@type_param_default`. Make it a real type alias with compiler support so it can be used in type annotations (e.g. `x: DefaultInt = 42`).
- `iter()` non-native: currently `@native("tpy::__iter__")` because regular functions can't return protocol types (`Iterator[T]`). Relax the protocol return type restriction for generic functions so `iter()` can have a real body (`return x.__iter__()`).
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- better class operator<< tests (but missing str formatting/concatenation)
- formatting/linting like in genweb
- existing C++ interoperability: when we want to call existing C++ we need to declare types/functions in TPy files, but without generation, only annotating how to use them in code
- auto-declare fields from `__init__`: extend to `self.f = expr` and `self.f = Constructor()` (Phase 1 param-only is done, see `docs/CONSTRUCTOR_DESIGN.md`)
- `__int__` equivalent for Int32 etc types (e.g. `__int32__` etc or prefixed: `__tpy_int32__`)
- hoisted variable slots: keep them at the lowest scope that satisfies lifetime, instead of always hoisting to function scope
- runtime `using` declarations in global namespace (`tpy.hpp`): generated code should use `tpy::` prefix instead of relying on `using tpy::BigInt` etc.
- refactor: consider merging `gen_module_init()` body generation into `gen_body()` helper (functions and methods already use it, but module init has too many special cases currently)
- panic show line number?
- decorator qualified syntax: @tpy.extern.native_c (3-level attribute access not yet supported)
- "did you mean" import hints for unresolved decorators (needs library resolution)
- @extern_c/@extern_cpp functions/classes etc
- `# tpy: range-check=off`
- extend int type configuration to AddressType/SizeType/PtrDiff (e.g. UInt32, Int32, Int32)
- diagnostics: trace "float spill" origin across assignments/expressions (e.g. accidental `/` instead of `//`) and surface root cause in downstream type mismatch errors
- extract c++ compiler interface
- analysis: when an object is passed to a function by reference but then copied, should we suggest passing as Own[]?
- sizeof() function
- logging, stream object printing, to log instead of __str__

## Standard Library

## Other
- Docstrings: silently skipped in codegen (harmless, but no introspection support)
- make a doc with TPy vs Python differences

## C++ Codegen Review Findings (2026-03-12, updated 2026-03-18)

### Systematic suboptimalities
- **[HIGH effort]** Double subscript in augmented assignment: `a[i] += v` emits `__setitem__(a, i, add_check(__getitem__(a, i), v))` -- two separate bounds checks + index normalization. Hand-written C++ would use a single indexed access. Fix needs a reference-based codegen path (e.g. `__getitem_ref__` or direct `operator[]`).
- **[HIGH effort]** Inherited fields body-assigned instead of member initializer list: child constructors without `super()` assign inherited `std::string`/container fields in the body (default-construct then assign) instead of via MIL or base-class constructor delegation. Adds an extra default construction per non-trivial inherited field.
- **[MED effort]** Double `__deref__()` call in auto-deref field access: `r.x + r.y` where `r` has user-defined `__deref__()` emits `r.__deref__().x + r.__deref__().y` -- two separate deref calls. Should generate a temporary for the deref result when multiple field/method accesses share the same deref base in one expression.
### Missed optimizations
- **[MED effort]** Dead branch elision in codegen: when a condition folds to a constant (`if (true)` / `if (false)`), emit only the live branch body instead of the entire if/else structure. Currently the folded constant is emitted and the C++ compiler optimizes it away, but this adds unnecessary work to C++ compilation which is already a bottleneck. Applies to Literal narrowing (`if mode == "rb":` inside a branch where mode is known), union overload specialization (`isinstance` checks), and any future constant-folding path. Pattern exists in `_gen_if_overload_specialized` for union isinstance -- generalize to all if-conditions.
- **[LOW]** String concat chain produces N-1 intermediate allocations: `a + b + c + d` emits left-associative nested `str_concat` calls, each allocating a temporary `std::string`. A codegen optimization detecting a chain of `+` on string-view operands could emit a single `reserve` + N `append` calls.
- **[LOW]** `__param_` copy for reassigned parameters: when a parameter is reassigned in the function body, codegen takes it by `const&` then copies into a mutable local. For BigInt/string params, taking by value instead would let the caller move. Only helps when caller passes an rvalue; for lvalue calls it's worse (forces copy at call site vs zero-cost `const&`). Also changes ABI (not API).

## Low Priority
- `= default` semantic gap: records with required `__init__` params currently emit `ClassName() = default;` if their C++ fields are all trivially constructible, bypassing the Python-level construction contract. Should emit `= delete` (or nothing) instead -- `std::optional<T>` and containers don't require default-constructibility, so the impact is limited to direct `T t;` / `T arr[N]` patterns which tpyc doesn't generate anyway. Fix: check `init_params` required count in `_fld_type_cpp_default_constructible` (same as `_is_default_constructible`). See `docs/CONSTRUCTOR_DESIGN.md` open question 4.
- None-seeded variable assigned in all branches stays `Optional[T]`: when `x = None` is followed by assignment in both the `if` and `else` branches (so every path guarantees a value), `x` is still typed `std::optional<T>` after the if/else block. Post-dominance analysis could demote it to `T` and skip the optional wrapper. Not a correctness issue -- output is identical -- but adds unnecessary runtime cost and less readable C++.

## Known Limitations
- Subscript narrowing: `if items[i] is not None:` does not narrow `items[i]`. Hard to make sound due to index aliasing and container mutation; would need invalidation on any container write.
- Pointer-local slot reuse: reassigned T* pointer-locals allocate a fresh `std::optional<T>` slot per assignment. The initial slot could be reused after reassignment instead of allocating a new one.
- Inherited constructor forwarding: multi-level inheritance (`Child -> Mid -> Base`) where intermediate classes have no `__init__` doesn't forward the base constructor. C++ generates `Child() = default;` only, so `Child(args)` fails. Workaround: add explicit `__init__` + `super().__init__()` at each level.
- INT type params on functions: parsing works (`def foo[T, N: int]`) but codegen crashes for non-stub functions -- `inferred_type_args` contains `int` values that `type_to_cpp()` can't handle. Need `type_param_kinds` on `FunctionInfo` + codegen fixes. Stubs (`@cpp_template`/`@native`) work fine.
- Union isinstance narrowing in ternary: `x if isinstance(x, str) else ...` where `x: str | int` would need `std::get<T>()` extraction, which requires statement-level codegen (variable declaration for the extracted value).

## ShedSkin examples
- score4
- mandelbrot
- nbody
