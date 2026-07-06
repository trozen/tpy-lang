# The bound writes the protocol's params in SWAPPED order (Pair[V, K] against
# a protocol declared Pair[K, V]), so binding must be positional, not by name:
# K binds the protocol's second param (str), V binds the first (int). A by-name
# binding bug would bind K=int/V=str and mis-type the returns (C++ build fail).
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


# bound is Pair[V, K]: V <- protocol param 0 (first/int), K <- param 1 (second/str)
def get_k[K, V, T: Pair[V, K]](x: T) -> K:      # tpyc: ok
    return x.second()


def get_v[K, V, T: Pair[V, K]](x: T) -> V:      # tpyc: ok
    return x.first()


def main() -> None:
    p = IntStr(42, "swapped")
    print(get_k(p))            # K = str (protocol's second param)
    print(get_v(p) + 1)        # V = int (protocol's first param)


main()
