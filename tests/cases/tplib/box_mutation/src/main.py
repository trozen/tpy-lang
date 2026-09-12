# Box[T] supports field mutation via auto-deref: `box.x = value`. Goes
# through @auto_readonly Box.__deref__ -> @auto_readonly get(), which
# emits a mutable T* overload for mutating contexts. CPython doesn't
# simulate the Deref protocol, so this test is TPy-only.
from tpy import int32
from tplib import Box


class State:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    b = Box(State(int32(1)))
    print(b.x)         # 1 (auto-deref read)

    b.x = int32(10)    # auto-deref write (regression guard for @auto_readonly __deref__)
    print(b.x)         # 10


main()
