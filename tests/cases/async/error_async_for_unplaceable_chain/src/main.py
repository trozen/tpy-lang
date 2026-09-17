# `async for` takes the same iterable rule as the sync and generator for-each:
# an lvalue chain deeper than a loan key can spell (`d.shelf.rows[0]`,
# `self.grid.rows[i]`, `cube[i][j]`) leaves every mutation of what is iterated
# invisible to the invalidation check, so the loop is refused rather than run
# unguarded (BUGS.md#iter-borrow-place-needs-hops).
# One hop still compiles: `sh.rows[i]` is the `hop` section and `sh.one` the
# `field_hop` section of async/async_for_warn_iter_mutation.
# Workaround: bind the intermediate (`sh = d.shelf` then `async for x in
# sh.rows[0]`) or the element itself.
import asyncio
from tpy import Own, int32


class SrcIter:
    cursor: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.cursor = 0
        self.limit = limit

    async def __anext__(self) -> int32:
        if self.cursor >= self.limit:
            raise StopAsyncIteration
        val = self.cursor
        self.cursor += 1
        return val


class Source:
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.limit = limit

    def __aiter__(self) -> Own[SrcIter]:
        return SrcIter(self.limit)


class Shelf:
    rows: list[Source]

    def __init__(self) -> None:
        self.rows = [Source(2)]


class Depot:
    shelf: Shelf

    def __init__(self) -> None:
        self.shelf = Shelf()


# the reject is reported at the `def` -- the resumable iter-setup lowering
# raises outside a statement context, as its `res.*` siblings do
async def drain(d: Depot) -> None:  # tpyc: error(/res.for_iter_borrow_unplaceable/)
    async for x in d.shelf.rows[0]:
        print("deep:", x)


# same tag from the other direction -- a subscript whose receiver is itself a
# subscript; unannotated only because the compile stops at the first error
async def drain_cube(cube: list[list[Source]]) -> None:
    async for x in cube[0][0]:
        print("cube:", x)


def main() -> None:
    asyncio.run(drain(Depot()))


main()
