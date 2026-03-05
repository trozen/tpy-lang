# TODO

## Next
- Warn on wasteful field reassignment in `__init__`: when a field is initialized unconditionally (member initializer list) and then reassigned inside control flow, warn that this constructs then discards the initial value. Relevant for heavy types like BigInt. Users can avoid this with a `@staticmethod` helper or (once supported) ternary isinstance.
- `Own[Self]` consuming methods: `def build(self: Own[Self]) -> Product` -- method takes ownership of self (pass by value, caller moves). Needs parser support for `self` type annotations, new calling convention in codegen, and init tracker integration. See B12 in FEATURE_ROADMAP.md.
- Union return copies non-value members: `f() -> A | B` returns `std::variant<A, B>` by value, copying record members. Python returns by reference. `std::variant<A&, B&>` is not valid C++. Params are fine (`&` for non-value unions). See `docs/UNION_TYPES_DESIGN.md` Known Semantic Gaps.
- Array/list -> Optional[Span] coercion at call sites: `ArrayList[Int32, 8](arr)` where `arr: Array[Int32, 3]` fails because `Array -> Span -> Optional[Span]` requires two implicit conversions. The generic constructor codegen path (`call_type` branch in `_gen_call`) also passes `expr.call_type` (the record type) as `target_type` instead of the param type, confusing the deref logic. Fix: apply explicit `std::span<const T>(arg)` wrapping when the param is `Optional[Span[T]]` and the arg is a span-compatible type.
- Generic string inference: `first[T]("hello", "world")` deduces `T=str` (std::string) instead of `T=StrView` (std::string_view). String literals passed to generic functions create unnecessary copies. The inference should prefer StrView when the literal is only read.
- Container element str copies (partially done): tuple subscript (`b = t[1]`) now infers string_view. Array subscript, record field access, and list/dict subscript still copy. Extending requires source-mutation tracking -- detecting when the source container/record is mutated or reassigned after a view is captured (e.g. `arr[0]="new"`, `p.name="x"`, `items.append()`). Without this, a string_view could dangle. See TODO in `is_view_compatible_source()` in `local_deduction.py`.
- Avoid copying expensive value types in for-loops and tuple unpack. `for x in items` where `items: list[int]` generates `for (tpy::BigInt x : items)`, copying every element. Same for tuple unpack: `a, b = get_pair()` with `b: BigInt` copies out of the tuple. For small value types (int32_t, double, string_view) copying is fine, but BigInt is heap-allocated and the copy is wasteful since the container/tuple outlives the usage scope. Fix: use `const auto&` binding for read-only "expensive" value types. Could use the same PendingType pattern as str inference -- resolve to `const&` when never mutated. Needs a way to distinguish cheap vs expensive value types (size threshold or explicit trait).
- Zero-copy Iterable[T] for-loop: `for x in items` where `items: Iterable[T]` currently uses the while-loop + `__next_opt__()` path, which copies each element into `std::optional<T>`. For NativeIterable types (dict, list, etc.) passed through `Iterable[T]`, the concrete type is a C++ range. The codegen could emit `if constexpr (std::ranges::input_range<T>) { for (auto& x : items) ... } else { while-loop }` to get zero-copy iteration when the concrete type supports it. Alternatively, a `tpy::as_range()` adapter could unify both paths into a single range-based for, avoiding loop body duplication.
- int/bool value provenance (e.g. assert i > 0, then cast to uint without check)
- flow-sensitive None narrowing: broaden current narrowing coverage where needed (e.g. more complex expression forms)
- Ptr narrowing: after `p is not None`, skip `deref_check()` and use direct `->` access (same idea as Optional narrowing but for raw pointers)
- Ptr null-provenance warning: consider warning when accessing through a Ptr with unknown provenance (similar to Optional access warnings). Design question: warn on all unknown-provenance access (noisy for function params) vs only when provenance is lost (was non-null, then reassigned from unknown source)?
- consider generating `__len__()` as `size()` member function for STL compatibility (`__len__`, `__getitem__`, `__setitem__` are already free functions in `dunder.hpp`)

## Bugs
- Generic Optional with non-value type instantiation: `Container[Point].get()` returns `std::optional<Point>` (correct in template) but caller generates `Point* vp = ...` (pointer repr for concrete record). After type substitution TypeParamRef is gone, so codegen doesn't know the type came from a generic context. Needs representation tracking across generic instantiation boundaries.

## Examples

## Investigate
- Readonly type unification: consider replacing `ReadOnlySpan[T]`/`ReadOnlyPtr[T]`/`ReadOnlySpanLike[T]` with parameterized `Span[readonly[T]]`/`Ptr[readonly[T]]`/`SpanLike[readonly[T]]`. Would simplify the type system (fewer distinct types), make readonly composable, and align naming. Design questions: how does `readonly[T]` interact with generic type params, protocol conformance, and coercion rules? Does `Span[readonly[T]]` mean the span itself is const or the elements are? (Rust distinguishes `&[T]` vs `&mut [T]` at the reference level, not the element level.)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- zig language: what it is, how is it different from C, what useful patterns can we learn

## Builtins
- type(); (in future `T = type(x); z = T()`)
- tpy.ctypes.CInt32
- deref()
- ptr() function? Auto-select Ptr vs ReadOnlyPtr based on binding mutability. Needs sema-level magic (mutability not in type, it's in binding context).

## Hard Problems
(see FEATURE_ROADMAP.md for tracked hard problems)

## Python features
- Protocol isinstance in ternary expressions: `x = a.foo() if isinstance(a, P1) else a.bar()` generates a runtime `?:` but both branches must be valid C++ at template instantiation time. Fix: generate an IIFE with `if constexpr` inside, e.g. `[&]() -> T { if constexpr (P1<T_a>) { return a.foo(); } else { return a.bar(); } }()`. This also enables single-line field init in `__init__` (goes into the C++ member initializer list instead of requiring unconditional pre-assignment + reassignment in branches).

- Allow `@runtime_checkable` decorator on protocols (no-op in tpyc, enables CPython compatibility for isinstance checks on user-defined protocols)
- `del x` (variable unbinding): complex in compiled context -- needs lifetime/scope analysis. Low priority.
- list/StaticList operator (+=, *, +, in), sort
- allow type annotation to use "" (forward decl)
- for-each: preserve loop variable after loop exit (if used after the loop)
- C-style for loop: reassigning loop variable affects iteration (differs from Python)

## Documentation
- language restriction documentation

## Random items
- AddressSanitizer test mode: add `--asan` flag to compile exec tests with `-fsanitize=address` to detect memory leaks, double-free, and use-after-free. Important before serious usage of `__copy__` + `__del__` patterns (e.g. CopyableBox[T]).
- Large value-type copy warning: estimate record sizes from fields and warn when Own[T] passes a large type by value (e.g. >128 bytes). For inline data there's no cheap move -- the bits are the object. Suggest Own[Box[T]] for cheap ownership transfer (moves a pointer) or Ptr[T]/ReadOnlyPtr[T] for borrowing. Stricter threshold in @noalloc contexts.
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
- `Span(list([1,2,3]))` not supported
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
- `Box[T]` `operator<<` prints raw pointer address instead of contained value; needs custom `__repr__`/`__str__` support

## Other
- Char → str coercion: only literals work (`c: Char = "x"`), variables can't convert to str
- Docstrings: silently skipped in codegen (harmless, but no introspection support)
- make a doc with TPy vs Python differences

## Code Review Items (2026-03-04)
- Hashable protocol params generate non-const refs (`T_x&` instead of `const T_x&`) in non-`@readonly` functions. Pre-existing issue for all readonly protocols used in non-readonly functions.
- `Hashable` (and other protocols like `Sized`, `Comparable`) are usable as type annotations without `from tpy import Hashable`. Undocumented implicit availability.
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
- Destructor drop flag shadowing in inheritance: when both parent and child have `__del__`, each class emits its own `bool __tpy_owned_ = true` field. The child's shadows the parent's, creating two independent flags that must stay in sync. Currently correct (each move op/destructor operates on its own class's flag), but fragile. Fix: emit `__tpy_owned_` only on the root class that introduces `__del__`; children inherit it without shadowing.
- Functions don't currently support INT type params (only TYPE)
- `readonly[T]` field mutation: sema enforces readonly on assignment targets, but codegen may not emit `const` for `readonly[str]` fields. Verify codegen emits `const` qualifier.
- Union isinstance narrowing in ternary: `x if isinstance(x, str) else ...` where `x: str | int` would need `std::get<T>()` extraction, which requires statement-level codegen (variable declaration for the extracted value).

## ShedSkin examples
- score4
- mandelbrot
- nbody
