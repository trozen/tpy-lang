# The temporary-source sibling of gen_frame_iter_frame_source: the frame stores
# the temp in `__for_src` and then borrows it through `__iter__`, so BOTH the
# source type and its frame-emitted __iter__ struct must be complete at the
# field declaration. Holder.__iter__ has two yields to keep it off the peephole.
from typing import Iterator
from tpy import Int32, Own


class Holder:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [5, 6]

    def __iter__(self) -> Iterator[Int32]:
        for x in self.items:
            yield x
            yield x


def make() -> Own[Holder]:
    return Holder()


# The leading yield forces a frame; the loop then iterates a temporary whose
# __iter__ is a frame too.
def g_resumable() -> Iterator[Int32]:
    yield 0
    for x in make():
        yield x


def main() -> None:
    for v in g_resumable():
        print(v)


main()
