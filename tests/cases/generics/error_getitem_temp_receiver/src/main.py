# A record __getitem__ returning a pointer-repr `Rec | None` hands back a
# borrow into the receiver; subscripting a TEMPORARY receiver would dangle
# (the temporary is freed at end of the full-expression). Rejected loudly --
# bind the receiver to a local (as `peek`/`main` in getitem_optional_ref do).
from tpy import Int32


class Rec:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


class Box:
    _v: Rec

    def __init__(self, v: Rec):
        self._v = v

    def __getitem__(self, want: Int32) -> Rec | None:
        if want > 0:
            return self._v
        return None


def main() -> None:
    p = Box(Rec(7))[1]  # tpyc: error(/subscript into a temporary would dangle/)
    if p is not None:
        print(p.x)


main()
