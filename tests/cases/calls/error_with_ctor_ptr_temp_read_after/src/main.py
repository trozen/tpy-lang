# A `with` target read after the statement that reaches a temporary of its
# head through a constructor's stored address: no borrow fact records the
# store, so nothing keeps the temporary for the name, and it is refused
# (BUGS.md#ctor-ptr-store-not-a-loan).
from tpy import Ptr, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Src:
    box: Box

    def __init__(self, n: int32) -> None:
        self.box = Box(n)


class Mgr:
    p: Ptr[Box]

    def __init__(self, b: Box) -> None:
        b.n += 1
        self.p = b

    def __enter__(self) -> Box:
        return self.p

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


def run() -> int32:
    with Mgr(Src(6).box) as b:  # tpyc: error(/'Mgr' keeps the address of a temporary this statement destroys/)
        pass
    return b.n


print(run())
