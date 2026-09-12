# with-statement `as`-target in a resumable generator where the bound value
# is a non-value type used after a yield. It is hoisted into the frame as a
# tpy::frame_slot<T> (deleted operator=), so _emit_with_enter must bind it via
# .emplace(...) rather than a plain `=`. Regression guard for that dispatch.
from typing import Iterator
from tpy import int32


class Item:
    val: int32
    def __init__(self, val: int32) -> None:
        self.val = val


class Resource:
    item: Item
    def __init__(self, seed: int32) -> None:
        self.item = Item(seed)

    def __enter__(self) -> Item:
        return self.item

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit")


def gen(n: int32) -> Iterator[int32]:
    with Resource(7) as v:
        i: int32 = 0
        while i < n:
            yield i
            i += 1
        yield v.val


def main() -> None:
    for x in gen(3):
        print(x)


main()
