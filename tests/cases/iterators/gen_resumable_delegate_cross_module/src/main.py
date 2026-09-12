# A resumable frame delegating to a generator defined in ANOTHER module: the
# `__for_src` field spells the callee's frame struct through its namespace.
import asyncio
from typing import Iterator
from tpy import int32, Own
import gensrc
from gensrc import Bag, Box, Src, chatty, guarded, pair, walk


class LocalBox[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    def two(self) -> Iterator[T]:
        yield self.items[0]
        yield self.items[1]


# free function, `from m import g`
def free_import() -> Iterator[int32]:
    yield 0
    for x in walk():  # tpyc: ok
        yield x


# free function reached through the module object, `import m; m.g()`
def module_call() -> Iterator[int32]:
    yield 10
    for x in gensrc.walk():  # tpyc: ok
        yield x


# generator method on an imported class
def imported_method(s: Src) -> Iterator[int32]:
    yield 20
    for x in s.steps():  # tpyc: ok
        yield x


# generic cross-module callee: the field carries the inferred type args
def generic_callee() -> Iterator[int32]:
    yield 30
    for x in pair(1, 2):  # tpyc: ok
        yield x


# generic OWNER, cross-module: namespace and owner type args both come off the
# receiver's rendered type
def generic_owner_imported(b: Box[int32]) -> Iterator[int32]:
    yield 40
    for v in b.two():  # tpyc: ok
        yield v


# generic OWNER, same module -- the leg the shared naming grammar lifts
def generic_owner_local(b: LocalBox[int32]) -> Iterator[int32]:
    yield 50
    for v in b.two():  # tpyc: ok
        yield v


# The embedded frame BORROWS its receiver: mutating `b` between pulls must be
# visible to the callee on its next resume, and to the caller after the loop.
def mutate_receiver(b: Bag) -> Iterator[int32]:
    yield 60
    for v in b.readings():  # tpyc: ok
        b.bump()
        yield v


# The embedded frame is pulled LAZILY: the callee runs only as far as each
# `__next__` demands, interleaved with the consumer's own work.
def lazy_interleave() -> Iterator[int32]:
    yield 70
    for x in chatty():  # tpyc: ok
        yield x


# Abandoned mid-delegation: the consumer breaks, and the callee's `finally`
# still runs when the frames are torn down.
def abandoned() -> Iterator[int32]:
    yield 80
    for x in guarded():  # tpyc: ok
        yield x


# async position: the same field, in a coroutine that suspends in the loop body
async def async_position() -> int32:
    total: int32 = 0
    for x in walk():  # tpyc: ok
        await asyncio.sleep(0)
        total += x
    return total


def main() -> None:
    for v in free_import():
        print("free:", v)
    for v in module_call():
        print("modcall:", v)
    for v in imported_method(Src(7)):
        print("method:", v)
    for v in generic_callee():
        print("generic:", v)
    for v in generic_owner_imported(Box([5, 6])):
        print("genowner-imported:", v)
    for v in generic_owner_local(LocalBox([8, 9])):
        print("genowner-local:", v)
    bag = Bag()
    for v in mutate_receiver(bag):
        print("mutate:", v)
    print("mutate: after", bag.n)
    for v in lazy_interleave():
        print("lazy:", v)
        print("lazy: consumer pulled")
    for v in abandoned():
        print("abandon:", v)
        if v == 1:
            break
    print("abandon: after break")
    print("async:", asyncio.run(async_position()))


main()
