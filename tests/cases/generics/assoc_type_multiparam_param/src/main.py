# A parameter typed as a 2-param protocol (not a bound) shares the same
# inference helper: K and V are solved positionally from the argument, not
# broadcast to a single type.
from typing import Protocol


class Pair[K, V](Protocol):
    def first(self) -> K: ...
    def second(self) -> V: ...


class IntStr:
    a: int
    b: str

    def __init__(self, a: int, b: str):
        self.a = a
        self.b = b

    def first(self) -> int:
        return self.a

    def second(self) -> str:
        return self.b


def show_v[K, V](x: Pair[K, V]) -> V:    # tpyc: ok
    return x.second()


def show_k[K, V](x: Pair[K, V]) -> K:    # tpyc: ok
    return x.first()


def main() -> None:
    p = IntStr(5, "zz")
    print(show_v(p))            # V = str
    print(show_k(p) + 1)        # K = int, distinct from V


main()
