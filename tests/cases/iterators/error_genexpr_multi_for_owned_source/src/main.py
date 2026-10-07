# A multi-clause generator expression never takes a source that yields owned
# values at clause 0; its refusal says so rather than naming the last-clause rule
# (docs/LANGUAGE_FEATURES.md, Generators); CPython runs it.
from tpy import int32, Own
from typing import Iterator


class W:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def widgets(k: int32) -> Iterator[Own[W]]:
    for i in range(k):
        yield W(i)


def main() -> None:
    print(sum(w.n + j for w in widgets(3) for j in range(2)))  # tpyc: error(/a generator expression cannot iterate a source that yields owned values/)


main()
