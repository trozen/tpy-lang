# The same owned local returned into two Own elements is copied into the
# first, and a record with __del__ has no copy: refused in sema rather than
# left to a deleted copy constructor in the C++ build.
from tpy import Own, int32


class G:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __del__(self) -> None:
        pass


def dup() -> tuple[Own[G], Own[G]]:
    g = G(1)
    return (g, g)  # tpyc: error(/non-copyable type 'G' is used after this point and cannot be moved into tuple element 0/)


def main() -> None:
    t = dup()
    print(t[0].v)


main()
