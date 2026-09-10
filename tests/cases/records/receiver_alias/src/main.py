# Receiver aliases retain identity, constness and rebinding across suspension.
# Mutations in both directions expose accidental copies of the receiver.
from __future__ import annotations

import asyncio
from typing import Iterator, Protocol, Self
from tpy import Int32, Own, dynamic, nocopy, readonly


class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n
        # Constructor: the alias observes the initialized receiver.
        me = self  # tpyc: ok
        me.n += 1

    def mutate(self) -> None:
        # Method: reads and writes share the same instance in both directions.
        me = self  # tpyc: ok
        me.n += 2
        self.n += 3
        print("method", self.n, me.n)

    @readonly
    def read(self) -> Int32:
        # Explicit readonly receiver produces a const reference alias.
        me = self  # tpyc: ok
        return me.n

    def inferred_read(self) -> Int32:
        # Inferred readonly receiver has the same alias representation.
        me = self  # tpyc: ok
        return me.n

    def nested(self) -> None:
        def inner() -> None:
            # Non-escaping closure: the alias still denotes the receiver.
            me = self  # tpyc: ok
            me.n += 4

        inner()
        try:
            # A distinct name avoids the separate nested-scope prescan collision.
            guarded = self  # tpyc: ok
            guarded.n += 5
        finally:
            print("closure_try", self.n)

    def generic[T](self, value: T) -> T:
        # Generic method and its concrete twin keep a receiver reference.
        me = self  # tpyc: ok
        me.n += 1
        return value

    def concrete(self, value: Int32) -> Int32:
        me = self  # tpyc: ok
        me.n += 1
        return value

    def reassign(self, other: Cell) -> None:
        # A later rebind requires a pointer alias from its initial self source.
        me = self  # tpyc: ok
        me.n += 1
        me = other
        me.n += 2
        # Rebinding back to self preserves the existing address conversion.
        me = self  # tpyc: ok
        me.n += 3
        print("reassign", self.n, other.n)

    def steps(self) -> Iterator[Int32]:
        # Generator: the alias remains live before and after suspension.
        me = self  # tpyc: ok
        me.n += 1
        yield me.n
        me.n += 2
        yield self.n

    def rebound_steps(self) -> Iterator[Int32]:
        first = self  # tpyc: ok
        # Existing alias-of-alias sources and rebinds to self share the frame.
        me = first
        yield me.n
        me = self  # tpyc: ok
        me.n += 1
        yield self.n

    @readonly
    def readonly_steps(self) -> Iterator[Int32]:
        # The frame alias and its captured receiver must agree on constness.
        me = self  # tpyc: ok
        yield me.n
        yield me.n

    async def update(self) -> Int32:
        # Async: the frame alias survives an actual suspension point.
        me = self  # tpyc: ok
        await asyncio.sleep(0)
        me.n += 6
        self.n += 7
        return me.n


@nocopy
class Consumed:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def finish(self: Own[Self]) -> Own[Self]:
        # Consuming receiver: aliasing borrows; only the final return moves.
        me = self  # tpyc: ok
        me.n += 1
        print("consuming", self.n, me.n)
        return self


@dynamic
class Tagged(Protocol):
    pass


class Base(Tagged):
    def narrowed(self) -> None:
        if isinstance(self, Derived):
            # Narrowed self already has a named lvalue; preserve that path.
            me = self  # tpyc: ok
            me.n += 1
            print("narrowed", self.n, me.n)


class Derived(Base):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def ordinary_aliases(a: Cell, b: Cell) -> None:
    # Shared helper inverse: ordinary parameters and reseatable pointer locals.
    direct = a  # tpyc: ok
    direct.n += 1
    pointer = a
    pointer = b
    indirect = pointer  # tpyc: ok
    indirect.n += 2
    print("ordinary", a.n, b.n, pointer.n)


def main() -> None:
    cell = Cell(10)
    print("constructor", cell.n)
    cell.mutate()
    print("readonly", cell.read(), cell.inferred_read())
    cell.n += 1
    print("readonly_changed", cell.read(), cell.inferred_read())
    cell.nested()
    print("generic", cell.generic(7), cell.concrete(7), cell.n)
    other = Cell(20)
    cell.reassign(other)
    ordinary_aliases(cell, other)

    # Receivers stay live and unmoved while each method frame borrows them.
    for value in cell.steps():
        print("generator", value, cell.n)
        cell.n += 10
    print("generator_done", cell.n)
    for value in cell.rebound_steps():
        print("generator_rebind", value, cell.n)
        cell.n += 10
    for value in cell.readonly_steps():
        print("readonly_generator", value, cell.n)
        cell.n += 10
    print("async", asyncio.run(cell.update()), cell.n)

    consumed = Consumed(40)
    returned = consumed.finish()
    print("returned", returned.n)
    derived = Derived(50)
    derived.narrowed()
    print("narrowed_caller", derived.n)


main()
