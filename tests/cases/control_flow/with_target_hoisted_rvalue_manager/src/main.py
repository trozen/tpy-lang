# A branch-hoisted `with` target aliases `__enter__()`'s result, so an OWNED
# (rvalue) manager cannot stay block-scoped: it is hoisted to function scope
# alongside the target's slot. Both manager shapes are covered here -- the rvalue
# `Reg(...)` and the function-scope lvalue `keep` -- because only the rvalue form
# is at risk and a case using just the lvalue would ratify the unsafe rule instead
# of bounding it.
#
# `__del__` is the observation: it writes a sentinel into the object, so a target
# still pointing at a destroyed manager reads that sentinel instead of the value.
# CPython does not guarantee WHEN the manager is dropped, only that the target
# keeps it reachable, so outliving the block is not observable there.
from tpy import int32


class Reg:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        self.n = -999


def rvalue_manager(flag: bool) -> int32:
    if flag:
        with Reg(11) as view:
            pass
    else:
        with Reg(22) as view:
            pass
    return view.n


def lvalue_manager(flag: bool) -> int32:
    keep = Reg(33)
    if flag:
        with keep as view:
            pass
    else:
        with keep as view:
            pass
    return view.n


def main() -> None:
    print(rvalue_manager(True))
    print(rvalue_manager(False))
    print(lvalue_manager(True))


main()
