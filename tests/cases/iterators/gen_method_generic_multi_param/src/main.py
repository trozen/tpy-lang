# Generic class with two type params + a multi-yield generator method
# using control flow that hits the resumable frame. Guards the
# `template <typename K, typename V>` header order + struct-name
# template arg spelling for methods on multi-param generic classes.
from typing import Iterator


class Pair[K, V]:
    k: K
    v: V

    def __init__(self, k: K, v: V) -> None:
        self.k = k
        self.v = v

    def stream(self, n: int) -> Iterator[V]:  # tpyc: ok
        i = 0
        while i < n:
            yield self.v
            i += 1


def main() -> None:
    p = Pair("hi", 100)
    for x in p.stream(3):
        print(x)


main()
