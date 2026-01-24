# TODO

## Next
- make examples/brainfuck.py work
- str type and string handling (see docs/LANGUAGE_FEATURES.md#strings for design)
- subscription assignment
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- extract c++ compiler interface
- language restriction documentation
- use std::cout instead of printf (in future -> define policies)
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- test diagnostic (compilation warnings and errors, annotate python test files which lines)
- consider removing Bool type and just keeping bool (or an alias, but not sure if it's needed)
- ultimate goal: make tpyc compile with tpyc
