# Rc[T] auto-deref via Deref[T] protocol: `r.x` / `r.method()` work without
# explicit .get() in TPy. CPython doesn't simulate the Deref protocol, so
# this test is TPy-only (see no_cpython.txt).
from tpy import Int32
from tplib import Rc, make_rc


class State:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def doubled(self) -> Int32:
        return self.x * 2


def main() -> None:
    r = make_rc(State(Int32(10)))
    print(r.x)            # auto-deref: 10
    print(r.doubled())    # auto-deref method: 20

    r.x = Int32(99)       # auto-deref write
    print(r.x)            # 99

    r2 = r.clone()
    r2.x = Int32(5)       # mutation through clone visible via r
    print(r.x, r2.x)      # 5 5


main()
