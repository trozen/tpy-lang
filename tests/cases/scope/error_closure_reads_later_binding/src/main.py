# A nested def reading an enclosing local the enclosing function binds only
# after the def. CPython reads the cell when the closure is called, so this
# program is valid there; TPy captures at the def and rejects it -- a stated
# limitation, BUGS.md#nested-def-reads-enclosing-local-bound-later.
from tpy import int32


def outer() -> int32:
    def inner() -> int32:
        # The capture of `x`, which `outer` binds two lines later.
        return x  # tpyc: error(/'x' is read by nested function 'inner' before 'outer' binds it on line 12/)
    x = 5
    return inner()


print(outer())
