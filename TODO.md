# TODO

## Next
- extract built-in function defintions to separate files
- Int32(10**20) overflow; BigInt->Int32 overflow checks
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- global variables lazy initialization in c++ (in init module method)
- basic import support; implement basic time module, with time() function
- `l = list[int]()` constructor syntax
- avoid using `auto` in generated c++ code, use explicit variable types
- make this work: `def f(l:list[int]): pass`, `f([])` (or `list()`)

## Python features
- dict full support
- str full support
- tuple, multiple returns
- `None` type, optional values, null pointers
- very basic std lib (sys.argv, time.time) with imports
- list slicing (`items[1:3]`)
- float type (in future also Float32/64)

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- make `range` a generator function
- extract c++ compiler interface
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

## ShedSkin examples
- score4
- mandelbrot
- nbody
