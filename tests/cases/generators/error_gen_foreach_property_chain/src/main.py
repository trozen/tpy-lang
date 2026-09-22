# Inside a resumable frame a @property for-each iterable is admitted only at ONE
# hop: the frame keeps `__for_it`/`__for_end` alive across every suspension, and
# past one hop no key spells the storage the iteration borrows, so no mutation of
# it could ever be matched against the iteration's loan
# (BUGS.md#iter-borrow-place-needs-hops). Without the reject the loop is a heap
# use-after-free the moment the body -- or the CALLER, between two resumes --
# appends to the returned container.
# The reject is the FRAME route's, so it applies exactly when the loop body
# SUSPENDS: the generator below yields inside the loop and the `async def` twin
# awaits inside it, so both take it (the generator is unannotated only because
# the compile stops at the first error, which the `async def` unit reports). The same `async def` with no suspension in
# the loop lowers on the sync route and compiles, as a plain function does
# (tests/cases/iterators/foreach_property_chain).
# Workaround: bind the intermediate record -- `inner = o.inner` then
# `for b in inner.items:` -- which is the one-hop spelling, admitted in a frame
# but not guarded against a caller's mutation between two resumes
# (BUGS.md#frame-iter-loan-blind-to-caller).
from typing import Iterator

from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Inner:
    boxes: list[Box]

    def __init__(self) -> None:
        self.boxes = [Box(1), Box(2)]

    @property
    def items(self) -> list[Box]:
        return self.boxes


class Outer:
    inner: Inner

    def __init__(self) -> None:
        self.inner = Inner()


# generator: the iterator outlives every yield
def gen(o: Outer) -> Iterator[int32]:
    for b in o.inner.items:
        yield b.n


async def tick() -> None:
    return


# async def: the await in the loop body is what puts it on the frame route
async def drain(o: Outer) -> None:  # tpyc: error(/res.for_iter_borrow_unplaceable/)
    for b in o.inner.items:
        await tick()
        print(b.n)


def main() -> None:
    for n in gen(Outer()):
        print(n)


main()
