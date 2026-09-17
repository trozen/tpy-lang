# Borrow-conflict warnings under `async for`. An iteration borrows the storage
# it iterates, and `async for` borrows it exactly as the sync for-each does:
# `__aiter__` is user code and may hand the frame an iterator holding a pointer
# into the iterable (the `ptr_iter` section), so the two routes file one loan
# through one registration and warn at the same granularity -- the container
# for a NAME iterable, the container plus the borrowed element for a one-hop
# element iterable, the field's own storage for a one-hop field iterable, with
# the two indices compared so a proven-distinct pair is silent. A deeper chain
# has no loan key and is rejected, pinned by
# `async/error_async_for_unplaceable_chain`.
import asyncio
from tpy import Own, Ptr, take_ptr


class SrcIter:
    cursor: int
    limit: int

    def __init__(self, limit: int) -> None:
        self.cursor = 0
        self.limit = limit

    async def __anext__(self) -> int:
        if self.cursor >= self.limit:
            raise StopAsyncIteration
        val = self.cursor
        self.cursor += 1
        return val


class Source:
    seen: list[int]
    limit: int

    def __init__(self, limit: int) -> None:
        self.seen = []
        self.limit = limit

    def __aiter__(self) -> Own[SrcIter]:
        return SrcIter(self.limit)

    def push(self, x: int) -> None:
        self.seen.append(x)


async def runner() -> None:
    src = Source(3)
    async for x in src:
        src.push(x)  # tpyc: warning(/Mutation of 'src'/)


# post_loop: the iterator loan expires with the `async for`, so mutating the
# iterable after the loop is fine.
async def post_loop() -> None:
    src = Source(2)
    async for x in src:
        pass
    src.push(9)  # tpyc: ok
    print("post_loop:", src.seen)


# nested: the INNER `async for` ends without expiring the outer loan, so the
# append to the outer iterable after it still warns.
async def nested() -> None:
    outer = Source(2)
    inner = Source(1)
    async for x in outer:
        async for y in inner:
            inner.push(y)  # tpyc: warning(/Mutation of 'inner'/)
        outer.push(x)  # tpyc: warning(/Mutation of 'outer'/)
    print("nested:", outer.seen, inner.seen)


class Shelf:
    rows: list[Source]
    one: Source

    def __init__(self) -> None:
        self.rows = [Source(2), Source(1)]
        self.one = Source(2)


# hop: a one-hop element iterable files the loan on the container the element
# came out of, so a mutating method on that element warns as certain
async def hop(sh: Shelf) -> None:
    async for x in sh.rows[0]:
        sh.rows[0].push(x)  # tpyc: warning(/Mutation of 'sh.rows\[\.\.\.\]' while iterating over it.*'push' invalidates the iterator/)
    print("hop:", sh.rows[0].seen)


# field_hop: a one-hop FIELD iterable files the loan on that field's own
# storage, so a mutating method on it warns just as the NAME spelling does
async def field_hop(sh: Shelf) -> None:
    async for x in sh.one:
        sh.one.push(x)  # tpyc: warning(/Mutation of 'sh.one' while iterating over it.*'push' invalidates the iterator/)
    # printed by length: a container read off a field is not yet an admitted
    # print argument (print.arg.container_field_access)
    print("field_hop:", len(sh.one.seen))


# elem_distinct: two int literals that differ cannot name one element, so this
# is valid Python and compiles clean
async def elem_distinct(rows: list[Source]) -> None:
    async for x in rows[0]:
        rows[1].push(x)  # tpyc: ok
    print("elem_distinct:", rows[1].seen)


# elem_unknown: a name may hold the literal's value, so the pair may alias and
# the warning says so rather than claiming a hit
async def elem_unknown(rows: list[Source], i: int) -> None:
    async for x in rows[0]:
        rows[i].push(x)  # tpyc: warning(/Mutation of 'rows\[\.\.\.\]' may hit the element being iterated.*'push' may invalidate the iterator/)
    print("elem_unknown:", rows[i].seen)


# elem_container: growing the container an element was iterated out of can
# reallocate the storage the iteration points into, whatever the index rule says
async def elem_container(rows: list[Source]) -> None:
    async for x in rows[0]:
        await asyncio.sleep(0)
        rows.append(Source(1))  # tpyc: warning(/Mutation of 'rows' while iterating over it.*'append' invalidates the iterator/)
        print("elem_container:", x)
    print("elem_container len:", len(rows))


class Feed:
    hits: list[int]
    lim: int

    def __init__(self, lim: int) -> None:
        self.hits = []
        self.lim = lim

    # the iterator holds a POINTER back into the iterable, so the frame's
    # iterator really does borrow it -- this is the shape that makes the loan
    # necessary rather than merely symmetric
    def __aiter__(self) -> "Own[FeedIter]":
        return FeedIter(take_ptr(self))

    def note(self, x: int) -> None:
        self.hits.append(x)


class FeedIter:
    p: Ptr[Feed]
    cur: int

    def __init__(self, p: Ptr[Feed]) -> None:
        self.p = p
        self.cur = 0

    async def __anext__(self) -> int:
        if self.cur >= self.p.lim:
            raise StopAsyncIteration
        v = self.cur
        self.cur += 1
        return v


# ptr_iter: the mutation is a method on the borrowed element itself, which the
# pointer survives; the warning is what stands between it and an append that
# would move the Feed out from under the iterator
async def ptr_iter(feeds: list[Feed]) -> None:
    async for x in feeds[0]:
        feeds[0].note(x)  # tpyc: warning(/Mutation of 'feeds\[\.\.\.\]' while iterating over it.*'note' invalidates the iterator/)
    print("ptr_iter:", feeds[0].hits)


def main() -> None:
    asyncio.run(runner())
    asyncio.run(post_loop())
    asyncio.run(nested())
    asyncio.run(hop(Shelf()))
    asyncio.run(field_hop(Shelf()))
    asyncio.run(elem_distinct([Source(2), Source(1)]))
    asyncio.run(elem_unknown([Source(2), Source(1)], 1))
    asyncio.run(elem_container([Source(2)]))
    asyncio.run(ptr_iter([Feed(2)]))


main()
