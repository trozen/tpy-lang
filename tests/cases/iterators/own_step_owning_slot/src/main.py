# A source whose declared step hands its objects over (an owning generator,
# a class with `__next__ -> Own[X]` or `__iter__ -> Iterator[Own[X]]`)
# fits an owning slot `Iterable[Own[X]]` and moves its steps, silently; a
# live list at that slot keeps its copy warning. Variants: protocol
# forwarding, adapters, further source shapes, dispatch, value steps.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, int32
from fixtures import Bag, Cell, Counter, TokBag, TokCursor, make, take, take_tok
import part_adapters
import part_dispatch
import part_protocol_forward
import part_source_shapes
import part_value_steps


def keep(xs: Iterable[Own[list[int32]]]) -> Own[list[list[int32]]]:
    out: list[list[int32]] = []
    for r in xs:
        out.append(r)
    return out


def take_g[T](xs: Iterable[Own[T]]) -> int32:
    n = 0
    for _ in xs:
        n += 1
    return n


class Summer:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def add(self, xs: Iterable[Own[Cell]]) -> None:
        for c in xs:
            self.total += c.v


def doubled(xs: Iterable[Own[Cell]]) -> Iterator[int32]:
    for c in xs:
        yield c.v * 2


async def atotal(xs: Iterable[Own[Cell]]) -> int32:
    s = 0
    for c in xs:
        s += c.v
    return s


def main() -> None:
    # free function, owning generator
    print("free make", take(make(3)))  # tpyc: ok
    # free function, `__next__ -> Own[Cell]` class
    print("free counter", take(Counter()))  # tpyc: ok
    # free function, `__iter__ -> Iterator[Own[Cell]]` class
    print("free bag", take(Bag()))  # tpyc: ok
    # a named handover source passed twice: a Bag hands out fresh cells
    # each pass, a Counter is exhausted by the first
    b = Bag()
    print("named bag 1", take(b))  # tpyc: ok
    print("named bag 2", take(b))  # tpyc: ok
    c = Counter()
    print("named counter 1", take(c))  # tpyc: ok
    print("named counter 2", take(c))  # tpyc: ok
    # @nocopy steps move: a copy would not compile
    print("nocopy bag", take_tok(TokBag()))  # tpyc: ok
    print("nocopy cursor", take_tok(TokCursor()))  # tpyc: ok
    # list() collects the handed-over steps
    xs = list(Bag())  # tpyc: ok
    xs[0].v += 1
    print("list bag", xs[0].v, xs[1].v)
    # generic owning slot
    print("generic", take_g(Bag()), take_g(Counter()))  # tpyc: ok
    # method parameter (a constructor's `Iterable[Own[T]]` parameter is a
    # codegen reject for every source: BUGS.md#ctor-own-iterable-param-reject)
    s = Summer()
    s.add(Bag())  # tpyc: ok
    s.add(Counter())  # tpyc: ok
    print("method", s.total)
    # generator parameter
    print("generator", list(doubled(Bag())))  # tpyc: ok
    # async parameter
    print("async", asyncio.run(atotal(Counter())))  # tpyc: ok
    # a live list at the owning slot copies its elements (warned: the
    # callee's writes land on the copies) and keeps them
    cells = [Cell(1), Cell(2)]
    print("live list", take(cells))  # tpyc: warning(/copies Cell elements/)
    print("live list after", len(cells))
    # a live list's elements at the owning slot are copied, so the
    # callee's moves leave the caller's rows whole
    rows: list[list[int32]] = [[1], [2]]
    kept = keep(rows)  # tpyc: warning(/copies list\[int32\] elements/)
    rows[0].append(5)
    print("live rows after", rows[0], rows[1], len(kept))
    part_protocol_forward.main()
    part_source_shapes.main()
    part_adapters.main()
    part_value_steps.main()
    part_dispatch.main()


main()
