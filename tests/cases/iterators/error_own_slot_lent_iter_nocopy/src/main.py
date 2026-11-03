# A user iterator lending a @nocopy element to an owning element slot is an error
# (docs/LANGUAGE_FEATURES.md "A user iterator lends at an owning element slot").
from typing import Iterable, Iterator
from tpy import Own, int32, nocopy


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


G = [N(1), N(2)]


class View:
    def __iter__(self) -> Iterator[N]:
        for x in G:
            yield x


def bump(source: Iterable[Own[N]]) -> None:
    for x in source:
        x.v += 99


def main() -> None:
    bump(View())  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage/)
    print(G[0].v)


main()
