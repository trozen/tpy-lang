# A local bound from an `Own[...]` getter owns its value, so a second local
# bound from it is an ordinary local-to-local binding: a move when the first
# is dead afterwards, an alias when it is still read. Both spellings of the
# accessor (`@property` and method) agree.
from tpy import Own, int32


class Src:
    _xs: list[int32]

    def __init__(self) -> None:
        self._xs = [1, 2]

    @property
    def own(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._xs:
            out.append(x)
        return out

    def own_m(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._xs:
            out.append(x)
        return out


# `v` is dead after the rebind
def prop_moved(s: Src) -> None:
    v = s.own
    v2 = v  # tpyc: ok
    v2.append(3)
    print("prop_moved", len(v2), len(s.own))


def meth_moved(s: Src) -> None:
    v = s.own_m()
    v2 = v  # tpyc: ok
    v2.append(3)
    print("meth_moved", len(v2), len(s.own_m()))


# `v` is read after the rebind, so `v2` aliases it: the append shows through
def prop_aliased(s: Src) -> None:
    v = s.own
    v2 = v  # tpyc: ok
    v2.append(3)
    print("prop_aliased", len(v), len(v2))


def meth_aliased(s: Src) -> None:
    v = s.own_m()
    v2 = v  # tpyc: ok
    v2.append(3)
    print("meth_aliased", len(v), len(v2))


def main() -> None:
    prop_moved(Src())
    meth_moved(Src())
    prop_aliased(Src())
    meth_aliased(Src())


main()
