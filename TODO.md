# TODO

## Next
- argument default values
- @dataclass
- int/bool value provenance (e.g. assert i > 0, then cast to uint without check)
- bi-directional contextual type inference (Phase 1b: coercion-aware matching, Phase 3: overload filtering by return type): docs/BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md
- class field instantiation design: Phase 2 (uninitialized field detection, init-list body-assign warning, conditional `= default`) -- see `docs/CONSTRUCTOR_DESIGN.md`
- `# tpy:` directives handling (including per-module `# tpy: default-int=...`)
- flow-sensitive None narrowing: broaden current narrowing coverage where needed (e.g. more complex expression forms)
- Ptr narrowing: after `p is not None`, skip `deref_check()` and use direct `->` access (same idea as Optional narrowing but for raw pointers)
- Ptr null-provenance warning: consider warning when accessing through a Ptr with unknown provenance (similar to Optional access warnings). Design question: warn on all unknown-provenance access (noisy for function params) vs only when provenance is lost (was non-null, then reassigned from unknown source)?
- consider generating `__len__()` as `size()` member function for STL compatibility (`__len__`, `__getitem__`, `__setitem__` are already free functions in `dunder.hpp`)
- `ValueType` protocol bound — `T: ValueType` would suppress copy warnings for generic fields, since value types copy silently
- refactor: `find_binop_overload` in `operators.py` uses `type_matches_numeric` directly instead of the unified matching in `overloads.py`; had to add a special-case for PendingStrType -- should delegate to `type_matches_strict` so new deferred types work automatically
- keyword arguments
- coerce int32 -> uint32?
- remove the need for __tpy_owned_ in destructor (moved out, when there is a single pointer in class and the pointer may not be null; can it also work for |None case?)

## Bugs
- REPL: arr=[1,2,3]; arr[-4]
- Generic Optional with non-value type instantiation: `Container[Point].get()` returns `std::optional<Point>` (correct in template) but caller generates `Point* vp = ...` (pointer repr for concrete record). After type substitution TypeParamRef is gone, so codegen doesn't know the type came from a generic context. Needs representation tracking across generic instantiation boundaries.
- Constructor codegen uses body assignments instead of initializer lists when `__init__` has control flow -- see `docs/CONSTRUCTOR_DESIGN.md` Phase 2 (init-list body-assign warning) and Future (improved init-list extraction).
- Fixed-width integer true division (`Int32 / Int32`) not supported: `__truediv__` is missing from fixed-width int type definitions in `tpyc/modules/tpy.py`. BigInt defines it (returns float via `tpy::truediv`), but Int8-64/UInt8-64 only have `__floordiv__`. Fix: add `__truediv__` to `_register_fixed_int()` casting both operands to double, returning float.

## Examples
- example: StaticList, using UninitArrayStorage

## Investigate
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- zig language: what it is, how is it different from C, what useful patterns can we learn
- Go: channels is a nice concept (for our needs fixed size channels would be great)

## Builtins
- type(); (in future `T = type(x); z = T()`)
- tpy.ctypes.CInt32
- deref()
- ptr() function?

## Hard Problems
- auto-detect readonly from method body analysis (bottom-up inference) -- currently dunders in IMPLICIT_READONLY_METHODS are implicitly readonly, but regular methods need explicit `@readonly`; auto-inference could remove the need for annotations in most cases
- handling cyclic imports (see Language Features Roadmap -- Longer-term)
- user-defined function/method overloads via `@overload` decorator (from `typing`). Each `@overload` body is the real implementation (unlike CPython where bodies are stubs). Maps directly to C++ overloads. Infrastructure already exists (overload resolution, type-checked params). (See Language Features Roadmap -- Near-term)

## Python features
- `@override` decorator (Python 3.12 `typing.override`): mark methods that override a parent/protocol method; error if the method doesn't actually override anything (typo protection). C++ codegen already emits `override` automatically.
- dict full support
- set
- tuple, multiple returns (mandelbrot TODOs)
- list slicing (`items[1:3]`)
- list/StaticList operator (+=, *, +, in), sort
- bytes type
- allow type annotation to use "" (forward decl)
- for-each: preserve loop variable after loop exit (if used after the loop)
- C-style for loop: reassigning loop variable affects iteration (differs from Python)
- `Self` type
- properties with getter/setter

## Documentation
- language restriction documentation

## Random items
- AddressSanitizer test mode: add `--asan` flag to compile exec tests with `-fsanitize=address` to detect memory leaks, double-free, and use-after-free. Important before serious usage of `__copy__` + `__del__` patterns (e.g. CopyableBox[T]).
- Large value-type copy warning: estimate record sizes from fields and warn when Own[T] passes a large type by value (e.g. >128 bytes). For inline data there's no cheap move -- the bits are the object. Suggest Own[Box[T]] for cheap ownership transfer (moves a pointer) or Ptr[T]/ReadOnlyPtr[T] for borrowing. Stricter threshold in @noalloc contexts.
- Box[T] with only Ptr[T] inside, unsafe_alloc/free/init/drop; compiler optimize Box|None to just pointer, None as nullptr (NOTE: library-level `tplib.Box[T]` already works using unsafe primitives; this is about compiler-native Box)
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- Char type location: currently in `builtins` module but feels like a tpy type. Python doesn't have `Char`. Decide: keep in builtins, move to tpy, or remove? Affects `from tpy import Char` which currently fails.
- ultimate goal: make tpyc compile with tpyc
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)
- option to change divide semantics (negative): Python vs C++
- do not stop at first error, generate source with special Invalid() type, that would be ignored in further lines, so that we get all errors from compilation
- better handling of tpy_panic -- exceptions in first version (later generation policy)
- c++ generation profiles: utf8 strings vs char strings
- static_cast<char> -- should rather use checked cast (policy based)
- `Own[T]` for argument passing: callee takes ownership (how to pass an object from pointer? require explicit copy?)
- support more dunder methods: `__hash__`, etc.
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
- investigate other backends than c++
- panic show line number?
- decorator qualified syntax: @tpy.extern.native_c (3-level attribute access not yet supported)
- "did you mean" import hints for unresolved decorators (needs library resolution)
- @extern_c/@extern_cpp functions/classes etc
- `# tpy: range-check=off`
- dead code detection
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

## Code Review Items (2026-01-27)
- Comparisons accept any types: `record == record` passes sema but may fail C++ if no operator==
- `list.extend` lacks type validation: element type mismatch not checked when types are related but not identical (e.g. `list[Int32].extend(list[int])` passes sema, fails C++)
- `and`/`or` return `bool` not operand: `1 and 2` returns `1` (bool), Python returns `2`

## Known Limitations
- Pointer-local slot reuse: reassigned T* pointer-locals allocate a fresh `std::optional<T>` slot per assignment. The initial slot could be reused after reassignment instead of allocating a new one.
- Top-level block scoping differs from Python: Variables declared inside `if`/`while`/`for` at module level are visible outside the block in Python but block-scoped in C++. Example: `if cond: x = 1` followed by `print(x)` works in Python but `x` is out of scope in generated C++. Fix requires hoisting declarations to module scope. (Note: `if`/`else` in functions is fixed — branch-declared vars are pre-declared before the if-statement.)
- Inherited constructor forwarding: multi-level inheritance (`Child -> Mid -> Base`) where intermediate classes have no `__init__` doesn't forward the base constructor. C++ generates `Child() = default;` only, so `Child(args)` fails. Workaround: add explicit `__init__` + `super().__init__()` at each level.
- No `Iterable[T]` protocol: `__iter__` support is structural (detected by `get_iter_element_type()`), not protocol-based. Can't write `def f(it: Iterable[T])` as a parameter type. Adding it requires return-type conformance checking in the protocol system — currently protocol conformance only checks type equality on method signatures, not whether a return type *conforms to* another protocol (e.g., `Counter` conforming to `OptIterator[T]`).
- Destructor drop flag shadowing in inheritance: when both parent and child have `__del__`, each class emits its own `bool __tpy_owned_ = true` field. The child's shadows the parent's, creating two independent flags that must stay in sync. Currently correct (each move op/destructor operates on its own class's flag), but fragile. Fix: emit `__tpy_owned_` only on the root class that introduces `__del__`; children inherit it without shadowing.
- Functions don't currently support INT type params (only TYPE)

## ShedSkin examples
- score4
- mandelbrot
- nbody
