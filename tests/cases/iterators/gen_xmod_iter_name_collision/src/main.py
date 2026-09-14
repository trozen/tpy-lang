# The local Bag's frame `__iter__` iterates the CROSS-MODULE bags.Bag. The
# struct it embeds is `bags::__gen_Bag___iter__`, already complete via that
# module's header, so it is not an emit-ordering edge here. Keying the edge on
# the bare name "Bag" instead resolves it to the LOCAL Bag.__iter__ -- a
# self-edge -- and the program is rejected as a recursive delegation.
#
# The local __iter__ is a frame emit unit of its own, which is what lets the
# name-keyed edge collide at all.
from typing import Iterator
import bags
from tpy import int32


class Bag:
    src: bags.Bag

    def __init__(self) -> None:
        self.src = bags.Bag()

    def __iter__(self) -> Iterator[int32]:
        for x in self.src:
            yield x
            yield x


def main() -> None:
    for v in Bag():
        print(v)


main()
