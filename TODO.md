# TODO

## Next
- trait type system (e.g. protocols), like Iterable, HasLength, CoercionToSpan etc. (docs/PROTOCOL_DESIGN.md)
- support generics in code
- True/False booleans
- extract c++ compiler interface
- `type()` function
- `str()`, `str(123)`, `int("123")`
- build/run in release mode
- import user defined modules (properly set `__name__` in module) (hardcoded now: `ALLOWED_IMPORTS = {"tpy", "time", "sys", "math", "typing"}`)
- require imports at the top of file, don't allow inline imports
- type containing an allocated object (e.g. `Box[T]`)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- `Int32` should be imported from tpy, so it matches CPython (maybe use `from typ import *`?)
- `tpy::__len__()` -- consider changing semantics, so that `__len__()` method is generated in C++ as `size()` member function
- inherit from protocol class to validate methods, e.g. `class MyIntList(Sequence[int])` or `class MyList[T](Sequence[T])`
- how to handle const methods like `__len__` or `__getitem__`; how to handle constness in TPy?
- template function implementation should be in some specific header file
- `DeRef` protocol?
- allow type annotation to use "" (forward decl)

## Python features
- dict full support
- str full support
- tuple, multiple returns (mandelbrot TODOs)
- `None` type, optional values, null pointers
- list slicing (`items[1:3]`)
- make sure docstrings work in every context

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- too many references to StaticListType, ArrayType, ListType etc in codegen_cpp.py, should be data driven
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

## Other
- Char → str coercion: only literals work (`c: Char = "x"`), variables can't convert to str
- Docstrings: silently skipped in codegen (harmless, but no introspection support)

## Code Review Items (2026-01-27)
- Class field defaults: strings emit `= hello` not `= "hello"`, bools emit `= True` not `= true`
- Comparisons accept any types: `record == record` passes sema but may fail C++ if no operator==
- `in` on strings: `1 in "abc"` accepted (should require str/Char on LHS)
- Unknown record types not rejected: `bar: UnknownType` passes sema, fails at C++ compile
- Unary `not` not type-checked: `not items` (container) passes sema, fails C++ compile
- `list.extend` lacks type validation: element type mismatch not checked
- `and`/`or` return Bool not operand: `1 and 2` returns `1` (bool), Python returns `2`
- `pass` emits `0;` statement instead of empty block or comment
- Semantic warnings collected but never printed by CLI

## ShedSkin examples
- score4
- mandelbrot
- nbody
