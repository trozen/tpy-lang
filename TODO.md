# TODO

## Next
- iterator roadmap - implement all features (see LANGUAGE_FEATURES.md)
- None safety: refine Optional comparison semantics for mixed Optional/non-Optional operands (post-phase-2 polish)
- user record `__getitem__`: generate both const and non-const overloads so `p = obj[i]` creates a shared pointer-local (matching CPython), not a copy. Currently `__getitem__` is const-only → returns `const T&` → can't take mutable `T*`... or maybe: generate mutable function for now, use `@readonly` contract later (and generate const version from readonly)
- how to mark turbo-python files? using .tp.py is not good since it breaks python packages; maybe add an `# tpy` or `# tpy: options...` comment at the top?
- class field instantiation design: should we explicitely create class members in constructor (e.g. `self.obj = Obj()`) or are class member type annotations enough (e.g. `obj: Obj`)? should we store inline by default OR should we use `Own[Obj]` to define inline members?
- better local/global variable type deduction (e.g. if multiple assignment but first is literal, it should be postponed to look at next etc)
- diagnostics: trace "float spill" origin across assignments/expressions (e.g. accidental `/` instead of `//`) and surface root cause in downstream type mismatch errors
- flow-sensitive None narrowing: broaden current narrowing coverage where needed (e.g. more complex expression forms)
- readonly effects on protocols: mark read contracts like `Sized.__len__` / `Sequence.__getitem__` as readonly, enforce conformance (impl must be readonly), and satisfy via conservative readonly inference when provable so existing code usually keeps working
- list literal contextual typing: when LHS has explicit annotation like `list[Int32 | None]`, allow compatible literals (`[]`, `[Int32(1)]`, `[None]`) via contextual element-type widening instead of strict inferred-list mismatch
- const/mutability design: how to handle const methods (`__len__`, `__getitem__`), method qualifiers (like `const` in C++), and the `mutation_methods` set — unify into a coherent constness model
- type containing an allocated object (e.g. `Box[T]`)
- type containing uninitialized elements, that can be explicitely intialized, building block(s) for other data structures (e.g. `BoxList[T]`, `BoxArray[T, N]`)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- `type()` function / compile-time type info
- extract c++ compiler interface
- `tpy::__len__()` -- consider changing semantics, so that `__len__()` method is generated in C++ as `size()` member function
- template function implementation should be in some specific header file
- `DeRef` protocol?
- allow type annotation to use "" (forward decl)
- `ValueType` protocol bound — `T: ValueType` would suppress copy warnings for generic fields, since value types copy silently
- proper string handling (STRING_HANDLING.md)
- Deduce generic type args from field annotation: `self.data = Array()` → `Array[T, N]()` when `data: Array[T, N]`
- differentiate between .py and .tp.py files - .tp.py files are for TurboPython dialect, may or may not run with regular CPython, or some behaviour may be different. TurboPython should make effort to run any .py file, but should warn/error if some features are not supported or behave differently.
- make a doc with TPy vs Python differences
- keyword arguments
- analysis: when an object is passed to a function by reference but then copied, should we suggest passing as Own[]?
- for-each: preserve loop variable after loop exit (if used after the loop)
- REPL: arr=[1,2,3]; arr[-4]
- better C++ code formatting? 4 space indentation (or tab?)

## Hard Problems
- handling cyclic imports
- allow method overloads?

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
- bytes type

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- language restriction documentation
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- Char type location: currently in `builtins` module but feels like a tpy type. Python doesn't have `Char`. Decide: keep in builtins, move to tpy, or remove? Affects `from tpy import Char` which currently fails.
- ultimate goal: make tpyc compile with tpyc
- BigInt: use second bit for int 63-126 bits long; do not allocate mzp_t, use low level GMP functions
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)
- option to change divide semantics (negative): Python vs C++
- do not stop at first error, generate source with special Invalid() type, that would be ignored in further lines, so that we get all errors from compilation
- better handling of tpy_panic -- exceptions in first version (later generation policy)
- c++ generation profiles: utf8 strings vs char strings; int literal default to int or Int32 (per module, function, build options?)
- static_cast<char> -- should rather use checked cast (policy based)
- `Own[T]` for argument passing: callee takes ownership (how to pass an object from pointer? require explicit copy?)
- support augmented arithmetic operators, like `__iadd__` for `+=` etc.
- support more dunder methods: `__bool__`, `__hash__`, etc.
- extract built-in function defintions to separate files (len, print)
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- ability to define `__str__` method (currently works as explicit call `obj.__str__()`, but `str(obj)` doesn't dispatch to it)
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
- C++ header ordering: inline method bodies in structs (constructors, methods) can't call free functions declared later in the header. Affects cases like `self.value = func()` when func is defined before the class in Python but its C++ forward declaration is emitted after the struct. Fix: emit function forward declarations before struct definitions, or move method bodies out-of-line.
- investigate other backends than c++
- panic show line number?
- generate const function variants for @readonly functions
- properly import annotations from tpy module (readonly, noalloc etc); should not be accessible without it; also, should support @tpy.readonly
- @extern_c/@extern_cpp functions/classes etc

## Other
- Char → str coercion: only literals work (`c: Char = "x"`), variables can't convert to str
- Docstrings: silently skipped in codegen (harmless, but no introspection support)

## Code Review Items (2026-01-27)
- Comparisons accept any types: `record == record` passes sema but may fail C++ if no operator==
- Unknown record types not rejected: `bar: UnknownType` passes sema, fails at C++ compile
- Unary `not` not type-checked: `not items` (container) passes sema, fails C++ compile
- `list.extend` lacks type validation: element type mismatch not checked
- `and`/`or` return `bool` not operand: `1 and 2` returns `1` (bool), Python returns `2`

## Known Limitations
- `str(numeric)` returns `std::string` but `str` type maps to `std::string_view` - storing result in variable creates dangling reference (UAF). Safe for inline use only (e.g., `print(str(42))`). Proper fix requires ownership tracking in type system.
- Own[T] local variable optimization: allow `return local_var` without copy() since C++ uses NRVO (Named Return Value Optimization). Currently requires explicit copy() for all lvalues.
- Top-level block scoping differs from Python: Variables declared inside `if`/`while`/`for` at module level are visible outside the block in Python but block-scoped in C++. Example: `if cond: x = 1` followed by `print(x)` works in Python but `x` is out of scope in generated C++. Fix requires hoisting declarations to module scope. (Note: `if`/`else` in functions is fixed — branch-declared vars are pre-declared before the if-statement.)

## ShedSkin examples
- score4
- mandelbrot
- nbody
