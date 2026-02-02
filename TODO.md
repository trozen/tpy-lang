# TODO

## Next
- Own[T] should require explicit copy() for lvalues: `return copy(self.value)` not `return self.value`; rvalues like `return T()` are OK without copy
- Type parameter bounds: class SortedList[T: Comparable]
- Integer type parameters: class FixedStack[T, N: int]
- protocol fields? `class P(Protocol): value: Int32`
- allow protocol inheritance, with protocol check
- allow inheritance of normal classes
- extract c++ compiler interface
- `type()` function
- build/run in release mode
- import user defined modules (properly set `__name__` in module) (hardcoded now: `ALLOWED_IMPORTS = {"tpy", "time", "sys", "math", "typing"}`)
- require imports at the top of file, don't allow inline imports
- type containing an allocated object (e.g. `Box[T]`)
- type containing uninitialized elements, that can be explicitely intialized, building block(s) for other data structures (e.g. `BoxList[T]`, `BoxArray[T, N]`)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- `Int32` should be imported from tpy, so it matches CPython (maybe use `from typ import *`?)
- `tpy::__len__()` -- consider changing semantics, so that `__len__()` method is generated in C++ as `size()` member function
- inherit from protocol class to validate methods, e.g. `class MyIntList(Sequence[int])` or `class MyList[T](Sequence[T])`
- how to handle const methods like `__len__` or `__getitem__`; how to handle constness in TPy?
- template function implementation should be in some specific header file
- `DeRef` protocol?
- allow type annotation to use "" (forward decl)
- sema/codegen_cpp refactoring, got too large, same for tpy_runtime.hpp
- warning for semantic differences: `self.field = var` copies in C++ but creates shared reference in Python (only for object types, not value types or literals)
- propert string handling (STRING_HANDLING.md)
- `mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend", "reverse", "__setitem__"}` - should rather have some method qualifier? like `const` in C++?

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
- support all dunder methods: comparison (`__eq__`, `__lt__`, `__gt__`, etc.), `__str__`, `__bool__` etc
- split tpy_runtime.hpp
- extract built-in function defintions to separate files (len, print)
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- ability to define `__str__` method
- `@staticmethod`
- better class operator<< tests (but missing str formatting/concatenation)
- `import time as _time` syntax
- dynamic dispatch
- full Iterable[T]/Iterator[T] support (with StopIteration exception converted UTH to next/has_next method/returning optional)
- formatting/linting like in genweb
- properties with getter/setter
- indexing: Int32 vs Int64 vs SizeType?

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

## ShedSkin examples
- score4
- mandelbrot
- nbody
