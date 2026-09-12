# A `with` target REUSED by a later, narrower `with`, then read after that one.
# The target aliases the second manager past its block, so that OWNED manager
# must outlive the block -- it is hoisted beside the target slot. The read must
# come BEFORE any rebind to matter: a reuse chain whose target is rebound before
# the next read needs no hoist, which is why the gate asks about a read before
# the rebind rather than a read anywhere later.
#
# __del__ writes a sentinel, so a target left pointing at a destroyed manager
# reads -999 instead of the value.
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


def probe(flag: bool) -> int32:
    with Reg(11) as view:
        pass
    if flag:
        with Reg(22) as view:
            pass
    return view.n


def rebound_needs_no_hoist() -> int32:
    with Reg(33) as v:
        pass
    with Reg(44) as v:
        pass
    return v.n


def main() -> None:
    print(probe(True))
    print(probe(False))
    print(rebound_needs_no_hoist())


main()
