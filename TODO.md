# TODO

## Next
- finish items from OWNERSHIP_DESIGN.md
- Optional class members: `self.field: Point | None` → `std::optional<Point>` inline storage
- None inference: `x = None; x = Point()` → auto-infer `Optional[Point]` without explicit annotation
- None safety: field/method access on `T | None` without prior `is not None` check is currently silently allowed, generating nullable pointer dereference (UB in C++). Need to decide approach: compile-time narrowing analysis (`if x is not None:` refines type), runtime null checks, or some combination.
- user record `__getitem__`: generate both const and non-const overloads so `p = obj[i]` creates a shared pointer-local (matching CPython), not a copy. Currently `__getitem__` is const-only → returns `const T&` → can't take mutable `T*`.
- class field instantiation design: should we explicitely create class members in constructor (e.g. `self.obj = Obj()`) or are class member type annotations enough (e.g. `obj: Obj`)? should we store inline by default OR should we use `Own[Obj]` to define inline members?
- all expected warnings/errors should be in `# tpyc` annotation
- parser: extract to separate module
- better local/global variable type deduction (e.g. if multiple assignment but first is literal, it should be postponed to look at next etc)
- investigate other backends than c++
- type containing an allocated object (e.g. `Box[T]`)
- type containing uninitialized elements, that can be explicitely intialized, building block(s) for other data structures (e.g. `BoxList[T]`, `BoxArray[T, N]`)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- `type()` function
- extract c++ compiler interface
- `tpy::__len__()` -- consider changing semantics, so that `__len__()` method is generated in C++ as `size()` member function
- how to handle const methods like `__len__` or `__getitem__`; how to handle constness in TPy?
- template function implementation should be in some specific header file
- `DeRef` protocol?
- allow type annotation to use "" (forward decl)
- `ValueType` protocol bound — `T: ValueType` would suppress copy warnings for generic fields, since value types copy silently
- propert string handling (STRING_HANDLING.md)
- `mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend", "reverse", "__setitem__"}` - should rather have some method qualifier? like `const` in C++?
- Deduce generic type args from field annotation: `self.data = Array()` → `Array[T, N]()` when `data: Array[T, N]`
- differentiate between .py and .tp.py files - .tp.py files are for TurboPython dialect, may or may not run with regular CPython, or some behaviour may be different. TurboPython should make effort to run any .py file, but should warn/error if some features are not supported or behave differently.
- make a doc with TPy vs Python differences
- keyword arguments
- analysis: when an object is passed to a function by references but then copied, should we suggest passing as Own[]?
- for-each: preserve loop variable after loop exit (if used after the loop)

## Polymorphism
- Implicit upcasting: `parent: Animal = Dog()` (child instance to parent type)
- Ptr constructor: `Ptr(value)` to explicitly create pointers
- Polymorphic coercion: `Dog` → `Ptr[Animal]` (child to parent pointer)
- Virtual dispatch (requires C++ `virtual` methods) - currently `self.method()` in parent uses static dispatch
- Protocol-typed local variables: allow protocol types as variable types (e.g. `seq: Sequence[Int32] = items`)

## Python features
- dict full support
- str full support
- tuple, multiple returns (mandelbrot TODOs)
- list slicing (`items[1:3]`)
- make sure docstrings work in every context
- list/StaticList operator (+=, *, +, in), sort

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- make `range` a generator function
- language restriction documentation
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- Char/Bool type location: currently in `builtins` module but feel like tpy types. Python has `bool` (so `Bool` may not be needed), but doesn't have `Char`. Decide: keep in builtins, move to tpy, or remove? Affects `from tpy import Char` which currently fails.
- ultimate goal: make tpyc compile with tpyc
- BigInt: use second bit for int 63-126 bits long; do not allocate mzp_t, use low level GMP functions
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)
- option to change divide semantics (negative): Python vs C++
- do not stop at first error, generate source with special Invalid() type, that would be ignored in further lines, so that we get all errors from compilation
- better handling of tpy_panic -- exceptions in first version (later generation policy)
- c++ generation profiles: utf8 strings vs char strings; int literal default to int or Int32 (per module, function, build options?)
- static_cast<char> -- should rather use checked cast (policy based)
- `arr[(arr.size() - 1)]` -- should use true negative indexing
- `Own[T]` for argument passing: callee takes ownership (how to pass an object from pointer? require explicit copy?)
- `copy()` builtin for explicit copying
- support augmented arithmetic operators, like `__iadd__` for `+=` etc.
- support more dunder methods: `__str__`, `__bool__`, `__hash__`, etc.
- extract built-in function defintions to separate files (len, print)
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- ability to define `__str__` method
- better class operator<< tests (but missing str formatting/concatenation)
- dynamic dispatch
- full Iterable[T]/Iterator[T] support (with StopIteration exception converted UTH to next/has_next method/returning optional)
- formatting/linting like in genweb
- properties with getter/setter
- indexing: Int32 vs Int64 vs SizeType?
- list[Ptr[Point]] not supported, but it should be, eventually
- `Span(list([1,2,3]))` not supported
- existing C++ interoperability: when we want to call existing C++ we need to declare types/functions in TPy files, but without generation, only annotating how to use them in code
- implicitely define class members by assigning in constructor (in @noalloc mode should warn about deducing int)
- `__int__` equivalent for Int32 etc types (e.g. `__int32__` etc or prefixed: `__tpy_int32__`)
- hoisted variable slots: keep them at the lowest scope that satisfies lifetime, instead of always hoisting to function scope
- refactor: consider merging `gen_module_init()` body generation into `gen_body()` helper (functions and methods already use it, but module init has too many special cases currently)

## Other
- Char → str coercion: only literals work (`c: Char = "x"`), variables can't convert to str
- Docstrings: silently skipped in codegen (harmless, but no introspection support)

## Code Review Items (2026-01-27)
- Comparisons accept any types: `record == record` passes sema but may fail C++ if no operator==
- Unknown record types not rejected: `bar: UnknownType` passes sema, fails at C++ compile
- Unary `not` not type-checked: `not items` (container) passes sema, fails C++ compile
- `list.extend` lacks type validation: element type mismatch not checked
- `and`/`or` return Bool not operand: `1 and 2` returns `1` (bool), Python returns `2`

## Known Limitations
- `str(numeric)` returns `std::string` but `str` type maps to `std::string_view` - storing result in variable creates dangling reference (UAF). Safe for inline use only (e.g., `print(str(42))`). Proper fix requires ownership tracking in type system.
- Own[T] local variable optimization: allow `return local_var` without copy() since C++ uses NRVO (Named Return Value Optimization). Currently requires explicit copy() for all lvalues.
- Top-level block scoping differs from Python: Variables declared inside `if`/`while`/`for` at module level are visible outside the block in Python but block-scoped in C++. Example: `if cond: x = 1` followed by `print(x)` works in Python but `x` is out of scope in generated C++. Fix requires hoisting declarations to module scope. (Note: `if`/`else` in functions is fixed — branch-declared vars are pre-declared before the if-statement.)

## ShedSkin examples
- score4
- mandelbrot
- nbody
