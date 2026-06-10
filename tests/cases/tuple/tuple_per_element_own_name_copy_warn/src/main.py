# An Own[T] source NOT at last use (ob is read again after), passed by NAME into
# a per-element-Own slot, COPIES into owned storage. This is warned (matching the
# scalar T->Own[T] copy) rather than the raw C++ error it used to produce. The
# copy is intended: ob stays usable after the call. (The param is const, so the
# copy can't be observed via callee mutation; read-only.)
from tpy import Int32, Own


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def sink(p: tuple[Own[Box], Int32]) -> Int32:
    return p[0].val + p[1]


def f(ob: Own[Box]) -> Int32:  # tpyc: warning(/Own\[Box\] param 'ob' is never consumed/)
    pair = (ob, 0)
    r = sink(pair)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    return r + ob.val


def main() -> None:
    print(f(Box(5)))


main()
