# TODO

## Next
- `ordered_map::insert_or_assign` rvalue key overload: currently takes `const K&`, causing double-copy for string keys in dict comprehensions and literals. Add `void insert_or_assign(K&& key, V value)` overload.
- Warn on wasteful field reassignment in `__init__`: when a field is initialized unconditionally (member initializer list) and then reassigned inside control flow, warn that this constructs then discards the initial value. Relevant for heavy types like BigInt. Users can avoid this with a `@staticmethod` helper or (once supported) ternary isinstance.
- Union return copies non-value members: `f() -> A | B` returns `std::variant<A, B>` by value, copying record members. Python returns by reference. `std::variant<A&, B&>` is not valid C++. Params are fine (`&` for non-value unions). See `docs/UNION_TYPES_DESIGN.md` Known Semantic Gaps.
- Avoid copying expensive value types in for-loops: `for x in items` where `items: list[int]` generates `for (tpy::BigInt x : items)`, copying every element. Would need `const auto&` for read-only expensive value types, but requires proving the container isn't mutated during iteration (escape analysis). Tuple unpack const-ref is done.
- Zero-copy Iterable[T] for-loop: `for x in items` where `items: Iterable[T]` currently uses the while-loop + `__next_opt__()` path, which copies each element into `std::optional<T>`. For NativeIterable types (dict, list, etc.) passed through `Iterable[T]`, the concrete type is a C++ range. The codegen could emit `if constexpr (std::ranges::input_range<T>) { for (auto& x : items) ... } else { while-loop }` to get zero-copy iteration when the concrete type supports it. Alternatively, a `tpy::as_range()` adapter could unify both paths into a single range-based for, avoiding loop body duplication.
- int/bool value provenance (e.g. assert i > 0, then cast to uint without check)
- flow-sensitive None narrowing: broaden current narrowing coverage where needed (e.g. more complex expression forms)
- ~~FlowFacts refactor~~: Done. `FlowFacts` frozen dataclass in `sema/flow_facts.py` replaces the positional tuple. Adding new flow-sensitive properties requires one field + one `_merge_sets()` call.
- Ptr null-provenance warning: consider warning when accessing through a Ptr with unknown provenance (similar to Optional access warnings). Design question: warn on all unknown-provenance access (noisy for function params) vs only when provenance is lost (was non-null, then reassigned from unknown source)?
- consider generating `__len__()` as `size()` member function for STL compatibility (`__len__`, `__getitem__`, `__setitem__` are already free functions in `dunder.hpp`)

## Bugs
- Int type param not substituted in user method return types: `copy() -> ArrayList[T, N]` generates `ArrayList<int32_t, N>` instead of `ArrayList<int32_t, 8>`. The int type param `N` is not resolved to its concrete value in codegen. Workaround: use `tpy.copy()` builtin instead.
- `isinstance(x, WrongProto)` on `Optional[Protocol]`: sema doesn't validate that the isinstance target protocol matches the declared protocol. E.g. `x: Sized | None` allows `isinstance(x, Printable)` without error, and codegen emits `!std::same_as<nullptr_t>` (null guard) instead of a concept check. Should either error or generate correct concept constraint.
- NativeIterable for-loop `auto&` binding: range-based for with `auto&` fails for iterators that yield rvalues (e.g. `dict.items()` yields tuple copies). Needs `const auto&` or `auto&&` binding for non-value element types when the iterator yields temporaries.
- Tuple literal with non-value element types: `list[tuple[str, Point]]` generates `std::tuple<std::string, Point&>` instead of `std::tuple<std::string, Point>`, causing C++ compilation failure (cannot bind non-const lvalue reference to rvalue). Affects any tuple literal containing a record or other non-value type.

## Examples

## Investigate
- Readonly type unification: consider replacing `ReadOnlySpan[T]`/`ReadOnlyPtr[T]`/`ReadOnlySpanLike[T]` with parameterized `Span[readonly[T]]`/`Ptr[readonly[T]]`/`SpanLike[readonly[T]]`. Would simplify the type system (fewer distinct types), make readonly composable, and align naming. Design questions: how does `readonly[T]` interact with generic type params, protocol conformance, and coercion rules? Does `Span[readonly[T]]` mean the span itself is const or the elements are? (Rust distinguishes `&[T]` vs `&mut [T]` at the reference level, not the element level.)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- zig language: what it is, how is it different from C, what useful patterns can we learn

## Builtins
- type(); (in future `T = type(x); z = T()`)
- tpy.ctypes.CInt32
- ptr() function? Auto-select Ptr vs ReadOnlyPtr based on binding mutability. Needs sema-level magic (mutability not in type, it's in binding context).

## Hard Problems
(see FEATURE_ROADMAP.md for tracked hard problems)

## Python features
- Protocol isinstance in ternary expressions: `x = a.foo() if isinstance(a, P1) else a.bar()` generates a runtime `?:` but both branches must be valid C++ at template instantiation time. Fix: generate an IIFE with `if constexpr` inside, e.g. `[&]() -> T { if constexpr (P1<T_a>) { return a.foo(); } else { return a.bar(); } }()`. This also enables single-line field init in `__init__` (goes into the C++ member initializer list instead of requiring unconditional pre-assignment + reassignment in branches).

- Allow `@runtime_checkable` decorator on protocols (no-op in tpyc, enables CPython compatibility for isinstance checks on user-defined protocols)
- `del x` (variable unbinding): complex in compiled context -- needs lifetime/scope analysis. Low priority.
- allow type annotation to use "" (forward decl)
- for-each: preserve loop variable after loop exit (if used after the loop)
- C-style for loop: reassigning loop variable affects iteration (differs from Python)

## Documentation
- language restriction documentation

## Random items
- AddressSanitizer test mode: add `--asan` flag to compile exec tests with `-fsanitize=address` to detect memory leaks, double-free, and use-after-free. Important before serious usage of `__copy__` + `__del__` patterns (e.g. CopyableBox[T]).
- Large value-type copy warning: estimate record sizes from fields and warn when Own[T] passes a large type by value (e.g. >128 bytes). For inline data there's no cheap move -- the bits are the object. Suggest Own[Box[T]] for cheap ownership transfer (moves a pointer) or Ptr[T]/ReadOnlyPtr[T] for borrowing. Stricter threshold in @noalloc contexts.
- Container element str copies: tuple subscript infers string_view, but array subscript, record field access, and list/dict subscript still copy. Extending requires source-mutation tracking (escape analysis) to ensure string_view doesn't dangle. See `is_view_compatible_source()` in `local_deduction.py`.
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- Char type location: currently in `builtins` module but feels like a tpy type. Python doesn't have `Char`. Decide: keep in builtins, move to tpy, or remove? Affects `from tpy import Char` which currently fails.
- ultimate goal: make tpyc compile with tpyc
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)
- option to change divide semantics (negative): Python vs C++
- c++ generation profiles: utf8 strings vs char strings
- static_cast<char> -- should rather use checked cast (policy based)
- `Own[T]` for argument passing: callee takes ownership (how to pass an object from pointer? require explicit copy?)
- support more dunder methods: `__eq__`, `__ne__`, `__contains__`, etc.
- extract built-in function defintions to separate files (len, print)
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
- Own[T] -- warn when type is large (always? or only in hot-path? noalloc?)
- sizeof() function
- logging, stream object printing, to log instead of __str__

## Standard Library

## Other
- Char → str coercion: only literals work (`c: Char = "x"`), variables can't convert to str
- Docstrings: silently skipped in codegen (harmless, but no introspection support)
- make a doc with TPy vs Python differences

## Code Review Items (2026-03-04)
- User records as dict keys: sema now blocks them, but the complete feature needs codegen to emit `std::hash<T>` specialization and `operator==` for records with `__hash__`/`__eq__`.

## Code Review Items (2026-01-27)
- Comparisons accept any types: `record == record` passes sema but may fail C++ if no operator==
- `and`/`or` return `bool` not operand: `1 and 2` returns `1` (bool), Python returns `2`

## Low Priority
- Module-level tuple unpack: missing `Final` reassignment guard (`x, y = ...` where `x` is already `Final` should error)
- Module-level tuple unpack: missing `narrowing.update_after_write()` (stale narrowing facts not cleared after overwrite)
- Module-level tuple unpack: missing `declared_var_types` tracking (`# tpyc: type()` test annotations won't validate on unpack lines)

## Known Limitations
- Pointer-local slot reuse: reassigned T* pointer-locals allocate a fresh `std::optional<T>` slot per assignment. The initial slot could be reused after reassignment instead of allocating a new one.
- Top-level block scoping differs from Python: Variables declared inside `if`/`while`/`for` at module level are visible outside the block in Python but block-scoped in C++. Example: `if cond: x = 1` followed by `print(x)` works in Python but `x` is out of scope in generated C++. Fix requires hoisting declarations to module scope. (Note: `if`/`else` in functions is fixed — branch-declared vars are pre-declared before the if-statement.)
- Inherited constructor forwarding: multi-level inheritance (`Child -> Mid -> Base`) where intermediate classes have no `__init__` doesn't forward the base constructor. C++ generates `Child() = default;` only, so `Child(args)` fails. Workaround: add explicit `__init__` + `super().__init__()` at each level.
- Functions don't currently support INT type params (only TYPE)
- `readonly[T]` field mutation: sema enforces readonly on assignment targets, but codegen may not emit `const` for `readonly[str]` fields. Verify codegen emits `const` qualifier.
- Union isinstance narrowing in ternary: `x if isinstance(x, str) else ...` where `x: str | int` would need `std::get<T>()` extraction, which requires statement-level codegen (variable declaration for the extracted value).

## ShedSkin examples
- score4
- mandelbrot
- nbody
