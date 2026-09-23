# A borrowed parameter returned as Own[T] is copied, and a record with
# __del__ has no copy: refused in sema, the same rule a @nocopy type gets,
# rather than a warning followed by a deleted copy constructor in C++.
from tpy import Own, int32


class G:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __del__(self) -> None:
        pass


def f(g: G) -> Own[G]:
    return g  # tpyc: error(/non-copyable type 'G' cannot be returned as return type Own\[G\]/)


def main() -> None:
    g = G(1)
    print(f(g).v)


main()
