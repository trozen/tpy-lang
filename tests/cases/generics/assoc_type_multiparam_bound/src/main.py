# Associated-type inference through a bound naming a 2-param protocol:
# both K and V are solved from the conformer's method signatures (a broadcast
# bug would bind K=V and mis-type take_second). Also covers a compound bound
# arg (Pair[list[K], V]) where K is recovered by recursing into the element.
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


class ListStr:
    xs: list[int]
    s: str

    def __init__(self, xs: list[int], s: str):
        self.xs = xs
        self.s = s

    def first(self) -> list[int]:
        return self.xs

    def second(self) -> str:
        return self.s


def take_first[K, V, T: Pair[K, V]](x: T) -> K:      # tpyc: ok
    return x.first()


def take_second[K, V, T: Pair[K, V]](x: T) -> V:     # tpyc: ok
    return x.second()


def head[K, V, T: Pair[list[K], V]](x: T) -> K:      # tpyc: ok
    return x.first()[0]


def main() -> None:
    p = IntStr(7, "hi")
    print(take_first(p) + 1)        # K = int
    print(take_second(p))           # V = str, distinct from K
    print(head(ListStr([3, 4], "q")) + 100)   # K = int via list[K]


main()
