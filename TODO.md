# TODO

## Next
- extract built-in function defintions to separate files
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- test diagnostic (compilation warnings and errors, annotate python test files which lines)

## Python features
- dict full support
- str full support
- tuple, multiple returns
- `None` type, optional values, null pointers
- very basic std lib (sys.argv, time.time) with imports
- list slicing (`items[1:3]`)

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

## ShedSkin examples
- score4
- mandelbrot
- nbody
