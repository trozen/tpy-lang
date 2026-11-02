# A nested def reading the enclosing function's loop variable after its
# loop. CPython reads the variable's last value (prints 14); TPy does not yet
# capture a loop-bound name in a nested def written after the loop -- a
# stated limitation, BUGS.md#nested-def-reads-enclosing-local-bound-later.
from tpy import int32


def closures() -> None:
    for i in range(3):
        pass

    def inner() -> int32:
        # The read of the loop variable from the nested def.
        return i + 12  # tpyc: error(/'i' is read by nested function 'inner' after the loop that binds it on line 9; TPy does not yet support reading a name bound in a loop from a nested function defined after that loop/)
    print(inner())


closures()
