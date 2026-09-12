# A branch-hoisted `with` target used ONLY from a nested def: the closure keeps
# the name live past the branch that owns the manager, so the manager has to
# outlive the branch too. A source-order scan sees no read after the statement
# and leaves the manager block-scoped, which dangles.
from tpy import int32


class Reg:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def outer(flag: bool) -> int32:
    if flag:
        with Reg(11) as v:
            pass
    else:
        with Reg(22) as v:
            pass

    # The only use of `v` -- through a capture, after both managers' blocks.
    # It mutates, so a copy at the boundary would read back the un-incremented
    # value instead of dangling quietly.
    def inner() -> int32:
        v.n += 1
        return v.n

    return inner()


def main() -> None:
    print(outer(True))
    print(outer(False))


main()
