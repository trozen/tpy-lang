# Rc[T] / Arc[T] auto-deref via Deref[T] protocol: `r.x` / `r.method()` work
# without explicit .get() in TPy, including field writes and mutation-through-
# clone aliasing. CPython doesn't simulate the Deref protocol, so this test is
# TPy-only (see no_cpython.txt).
from tpy import int32
from tplib import Rc
from tplib.arc import Arc


class State:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def doubled(self) -> int32:
        return self.x * 2


def main() -> None:
    r = Rc.new(State(int32(10)))
    print(r.x)            # auto-deref: 10
    print(r.doubled())    # auto-deref method: 20

    r.x = int32(99)       # auto-deref write
    print(r.x)            # 99

    r2 = r.clone()
    r2.x = int32(5)       # mutation through clone visible via r
    print(r.x, r2.x)      # 5 5

    # Arc auto-derefs identically (the atomic-refcount sibling of Rc).
    a = Arc.new(State(int32(10)))
    print(a.x)            # auto-deref: 10
    print(a.doubled())    # auto-deref method: 20

    a.x = int32(99)       # auto-deref write
    print(a.x)            # 99

    a2 = a.clone()
    a2.x = int32(5)       # mutation through clone visible via a
    print(a.x, a2.x)      # 5 5


main()
