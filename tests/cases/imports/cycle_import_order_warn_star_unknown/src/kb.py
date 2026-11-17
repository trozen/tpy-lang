# A star import while the source's `__all__` is not a literal is not judged:
# TPy binds f with no warning, CPython copies nothing
# (BUGS.md#cycle-star-nonliteral-all-unjudged).
from ka import *  # tpyc: ok


def main() -> None:
    print("unknown __all__:", f())
