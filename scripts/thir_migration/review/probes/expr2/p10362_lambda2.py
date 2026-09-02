from tpy import Int32
from typing import Callable
class H:
    cb: Callable[[Int32], None]
    def __init__(self, cb: Callable[[Int32], None]) -> None:
        self.cb = cb
def a1() -> None:
    xs: list[Int32] = []
    f: Callable[[Int32], None] = lambda x: xs.append(x)
    f(1)
    print(len(xs))
def a2() -> None:
    xs: list[Int32] = []
    h = H(lambda x: xs.append(x))
    h.cb(1)
    print(len(xs))
def a3() -> None:
    d = {"b": 2, "a": 1}
    ks = sorted(d.keys(), key=lambda k: d[k])
    print(ks[0])
def a4() -> None:
    xs: list[Int32] = []
    h = H(lambda x: print(x))
    h.cb = lambda x: xs.append(x)
    h.cb(1)
    print(len(xs))
a1(); a2(); a3(); a4()
