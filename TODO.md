# TODO

## Next
- make a doc with TPy vs Python differences
- require importing tpy items, not autoimport (preferred `from tpy import *`)
- import user defined modules (properly set `__name__` in module) (hardcoded now: `ALLOWED_IMPORTS = {"tpy", "time", "sys", "math", "typing"}`)
- require imports at the top of file, don't allow inline imports
- type containing an allocated object (e.g. `Box[T]`)
- type containing uninitialized elements, that can be explicitely intialized, building block(s) for other data structures (e.g. `BoxList[T]`, `BoxArray[T, N]`)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- `type()` function
- extract c++ compiler interface
- `Int32` should be imported from tpy, so it matches CPython (maybe use `from typ import *`?)
- `tpy::__len__()` -- consider changing semantics, so that `__len__()` method is generated in C++ as `size()` member function
- how to handle const methods like `__len__` or `__getitem__`; how to handle constness in TPy?
- template function implementation should be in some specific header file
- `DeRef` protocol?
- allow type annotation to use "" (forward decl)
- warning for semantic differences: `self.field = var` copies in C++ but creates shared reference in Python (only for object types, not value types or literals)
- propert string handling (STRING_HANDLING.md)
- `mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend", "reverse", "__setitem__"}` - should rather have some method qualifier? like `const` in C++?
- Deduce generic type args from field annotation: `self.data = Array()` → `Array[T, N]()` when `data: Array[T, N]`

## Polymorphism
- Implicit upcasting: `parent: Animal = Dog()` (child instance to parent type)
- Ptr constructor: `Ptr(value)` to explicitly create pointers
- Polymorphic coercion: `Dog` → `Ptr[Animal]` (child to parent pointer)
- Virtual dispatch (requires C++ `virtual` methods) - currently `self.method()` in parent uses static dispatch

## Python features
- dict full support
- str full support
- tuple, multiple returns (mandelbrot TODOs)
- `None` type, optional values, null pointers
- list slicing (`items[1:3]`)
- make sure docstrings work in every context
- list/StaticList operator (+=, *, +, in), sort

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- make `range` a generator function
- language restriction documentation
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- consider removing Bool type and just keeping bool (or an alias, but not sure if it's needed)
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
- Import aliases don't work for type annotations: `from tpy import Int32 as I` allows `I(42)` but not `x: I`. Type parsing needs to consult imported aliases to support this.
- Own[T] local variable optimization: allow `return local_var` without copy() since C++ uses NRVO (Named Return Value Optimization). Currently requires explicit copy() for all lvalues.

## ShedSkin examples
- score4
- mandelbrot
- nbody
