# The local Bag's frame `__iter__` iterates the CROSS-MODULE bags.Bag. The
# struct it embeds is `bags::__gen_Bag___iter__`, already complete via that
# module's header, so it is not an emit-ordering edge here. Keying the edge on
# the bare name "Bag" instead resolves it to the LOCAL Bag.__iter__ -- a
# self-edge -- and the program is rejected as a recursive delegation.
#
# The local __iter__ must have TWO yields: a single-yield one takes the
# simple-generator lambda and is never an emit unit, so the collision could not
# fire and the case would pass vacuously.
from typing import Iterator
import bags
from tpy import Int32


class Bag:
    src: bags.Bag

    def __init__(self) -> None:
        self.src = bags.Bag()

    def __iter__(self) -> Iterator[Int32]:
        for x in self.src:
            yield x
            yield x


def main() -> None:
    for v in Bag():
        print(v)


main()
