# A tuple parameter taking one leaf over (`Own`) and passing the other on:
# each leaf of a literal argument is handed as its slot takes it -- the
# owned leaf escapes into the callee, the passed leaf follows the call --
# so a loop target passed through the borrowed leaf of a consumed call is
# no escape. The copies the slots warn of are the intended semantics.
import asyncio
from typing import Iterator
from tpy import Own, Ptr, int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class W:
    p: Ptr[Src]

    def __init__(self, s: Src) -> None:
        self.p = s


def gen_src(s: Src) -> Iterator[Src]:
    yield s


def take_mixed(out: list[W], p: tuple[Own[W], Src]) -> int32:  # tpyc: warning(/never consumed/)
    out.append(p[0])  # tpyc: warning(/copies W into owned storage/)
    return p[1].n


# The `Own` leaf is read in place: the section needs it owned beside the
# borrowed one, and the warning says it is never consumed.
def use_mixed(p: tuple[Own[Src], Src]) -> int32:  # tpyc: warning(/never consumed/)
    return p[0].n + p[1].n


async def independent() -> int32:
    out: list[W] = []
    dummy = Src(99)
    total = 0
    for i in range(3):
        for s in gen_src(Src(i * 10 + 1)):  # tpyc: ok
            total += take_mixed(out, (W(dummy), s))
        await asyncio.sleep(0)
    for w in out:
        total += w.p.n
    return total


async def fresh_copy() -> int32:
    total = 0
    for i in range(3):
        for s in gen_src(Src(i * 10 + 1)):  # tpyc: ok
            total += use_mixed((Src(s.n), s))
        await asyncio.sleep(0)
    return total


def main() -> None:
    n = asyncio.run(independent())
    print("mixed independent", n)
    n = asyncio.run(fresh_copy())
    print("mixed fresh copy", n)
