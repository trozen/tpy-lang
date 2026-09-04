# The module-scope twin of the kept-manager hoist: a `with` target reused by a
# later `with` and read after it aliases that manager past its block, so the
# manager slot must outlive the block -- at module scope that is the `static`
# initializer-scope slot, not a frame slot.
#
# __del__ writes a sentinel, so a target left pointing at a destroyed manager
# reads -999 instead of the value.
from tpy import Int32


class Reg:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        self.n = -999


with Reg(11) as view:
    pass

# The reuse whose manager is kept alive by the read below.
with Reg(22) as view:
    pass

print(view.n)
