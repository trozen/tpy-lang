# TODO

## Next
- option to output python source in generated c++ code in comments, for inspection (with an option, should be enabled in tests)
- full `list` type support
- generic `for` over lists/collections
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- Int32 etc operation range checks
- use std::cout instead of printf (in future -> define policies)
- extract built-in function defintions to separate files

## Python features
- dict full support
- str full support
- tuple, multiple returns
- `None` type, optional values, null pointers
- very basic std lib (sys.argv, time.time) with imports

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- extract c++ compiler interface
- language restriction documentation
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- test diagnostic (compilation warnings and errors, annotate python test files which lines)
- consider removing Bool type and just keeping bool (or an alias, but not sure if it's needed)
- ultimate goal: make tpyc compile with tpyc
- BigInt: use second bit for int 63-126 bits long; do not allocate mzp_t, use low level GMP functions
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)

## ShedSkin examples
- score4
- mandelbrot
- nbody