# A tuple element read out of a list whose type comes from its literal is
# compiled at the literal's settled element type, whatever consumes it.
import asyncio
from typing import Iterator
from tpy import int32, int64


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def total(t: tuple[int32, Box]) -> int32:
    return t[0] + t[1].v


def bump(t: tuple[int32, Box]) -> None:
    t[1].v = t[1].v + 10


def unpack_value() -> None:
    # function: a value-tuple element unpacked.
    xs = [(1, 2), (3, 4)]
    a, b = xs[1]  # tpyc: ok
    print("fn.unpack_value", a, b)


def unpack_record() -> None:
    # function: a record-member tuple element unpacked; the name aliases the
    # element's record, so the mutation shows through the list.
    xs = [(1, Box(2))]
    a, b = xs[0]  # tpyc: ok
    b.v = 9
    print("fn.unpack_record", a, xs[0][1].v)


def as_argument() -> None:
    # function: the element at a tuple parameter, by index and from the end.
    xs = [(1, Box(2)), (3, Box(4))]
    print("fn.argument", total(xs[0]), total(xs[-1]))  # tpyc: ok
    # The callee writes through the element it was handed.
    bump(xs[0])  # tpyc: ok
    print("fn.argument_alias", xs[0][1].v)


def local_binding() -> None:
    # function: a local bound to the element aliases it.
    xs = [(1, Box(2))]
    t = xs[0]  # tpyc: ok
    t[1].v = 7
    print("fn.local", t[0], xs[0][1].v)


def float_member() -> None:
    # function: a float literal member.
    xs = [(1.5, Box(2))]
    f, b = xs[0]  # tpyc: ok
    b.v = 8
    print("fn.float_member", f, xs[0][1].v)


def nested() -> None:
    # function: an element of a nested literal list.
    rows = [[(1, Box(2))], [(3, Box(4))]]
    bump(rows[1][0])  # tpyc: ok
    print("fn.nested", total(rows[1][0]), rows[1][0][1].v)  # tpyc: ok


class Reader:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def read(self) -> None:
        # method: the same reads.
        xs = [(5, Box(6))]
        a, b = xs[0]  # tpyc: ok
        b.v = 1
        self.n = a + total(xs[0])  # tpyc: ok
        print("method.read", self.n, xs[0][1].v)


class Built:
    n: int32

    def __init__(self) -> None:
        # constructor: the same reads.
        xs = [(1, Box(2))]
        a, b = xs[0]  # tpyc: ok
        b.v = 3
        self.n = a + total(xs[0])  # tpyc: ok


async def co_reads() -> int32:
    # async: an unpacked element used after a suspension.
    xs = [(1, Box(2))]
    a, b = xs[0]  # tpyc: ok
    await asyncio.sleep(0)
    b.v = 5
    return a + total(xs[0])  # tpyc: ok


def closure_reads() -> None:
    # closure: a nested def reading the captured list.
    xs = [(1, Box(2))]

    def inner() -> int32:
        a, b = xs[0]  # tpyc: ok
        b.v = 6
        return a + total(xs[0])  # tpyc: ok
    print("closure.read", inner(), xs[0][1].v)


def comp_reads() -> None:
    # comprehension: the element by index and as the loop target.
    xs = [(1, Box(2)), (3, Box(4))]
    print("comp.read", [total(xs[i]) for i in range(2)],  # tpyc: ok
          [a for a, b in xs])


def match_reads() -> None:
    # match arm: an unpack inside the arm.
    xs = [(1, Box(2))]
    k: int32 = 1
    match k:
        case 1:
            a, b = xs[0]  # tpyc: ok
            b.v = 7
            print("match.read", a, xs[0][1].v)
        case _:
            pass


def gen_reads() -> Iterator[int32]:
    # generator: reads on both sides of a suspension.
    xs = [(1, 2), (3, 4)]
    a, b = xs[0]  # tpyc: ok
    yield a
    c, d = xs[1]  # tpyc: ok
    yield b + c + d
    # ... and a record-member tuple held in the frame.
    rs = [(5, Box(6))]
    e, r = rs[0]  # tpyc: ok
    yield e
    r.v = 9
    yield rs[0][1].v


def scalar_read() -> None:
    # A bare literal element is still resolved by its consumer.
    zs = [1, 2]
    n: int64 = zs[1]  # tpyc: ok
    print("fn.scalar", n + 5000000000)


def dict_read() -> None:
    # A dict literal's tuple value reads the same way.
    d = {"k": (1, Box(2))}
    a, b = d["k"]  # tpyc: ok
    print("fn.dict", a, b.v)


def main() -> None:
    unpack_value()
    unpack_record()
    as_argument()
    local_binding()
    float_member()
    nested()
    Reader().read()
    print("ctor.read", Built().n)
    print("async.read", asyncio.run(co_reads()))
    closure_reads()
    comp_reads()
    match_reads()
    print("gen.reads", list(gen_reads()))
    scalar_read()
    dict_read()


main()
