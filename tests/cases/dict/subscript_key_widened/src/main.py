# A loop variable used as an argument decides nothing about its list where
# its width cannot pick the variant (every variant taking a number declares
# int32 there): once a later store widens the elements to int64, the
# argument narrows into the int32 parameter with a range check -- the
# subscript, the explicit dunder call, `.get()`, a user overload set and a
# user `__getitem__` set alike; a lookup warns. A variant whose parameter
# there takes no number (a callable) does not compete for the width.
from tpy import Fn, dispatch, int32, int64


def wide() -> int64:
    return 7


@dispatch
def take(k: int32) -> str:
    return "int"


@dispatch
def take(k: str) -> str:
    return "str"


@dispatch
def take_pos(k: int32, tag: int32) -> int32:
    return k


@dispatch
def take_pos(k: Fn[[int32], int32], tag: str) -> int32:
    return k(1)


class T:
    def __init__(self) -> None:
        pass

    @dispatch
    def take(self, k: int32) -> str:
        return "int"

    @dispatch
    def take(self, k: str) -> str:
        return "str"


class Table:
    cells: list[int32]

    def __init__(self) -> None:
        self.cells = [5, 6, 7]

    @dispatch
    def __getitem__(self, i: int32) -> int32:
        return self.cells[i]

    @dispatch
    def __getitem__(self, name: str) -> int32:
        return -1


def main() -> None:
    d: dict[int32, str] = {1: "a", 2: "b"}
    t = T()
    tab = Table()
    xs = [1, 2]
    for x in xs:
        # sub: the key is an argument to the dict's `__getitem__`
        print("sub", d[x])  # tpyc: warning(/lookup key narrows from int64 to int32/)
        # call: the dunder written out
        print("call", d.__getitem__(x))  # tpyc: warning(/lookup key narrows from int64 to int32/)
        # get: a lookup method with several variants
        print("get", d.get(x, "none"))  # tpyc: warning(/lookup key narrows from int64 to int32/)
        # free: a user overload set
        print("free", take(x))  # tpyc: ok
        # meth: a user method overload set
        print("meth", t.take(x))  # tpyc: ok
        # rec: a user `__getitem__` set, int32 or str
        print("rec", tab[x], tab["a"])  # tpyc: ok
        # fnpos: only the int32 variant takes a number at `k`; the other's
        # callable `k` makes the call Callable-bearing
        print("fnpos", take_pos(x, 0))  # tpyc: ok
    xs.append(wide())


main()
