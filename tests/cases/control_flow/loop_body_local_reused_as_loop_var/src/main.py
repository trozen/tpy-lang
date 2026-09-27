# A name first bound in a loop body and then reused as a later `for` loop
# variable is ONE local when the name is read after that loop: a zero-trip
# head leaves the body's value in it.
import asyncio
from typing import Iterator, Optional
from tpy import int32


# free function, `for` body binds first
def for_body(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    for n in xs:  # tpyc: ok
        print("for_body in", n)
    print("for_body after", n)


# `while` body binds first
def while_body(xs: list[int32]) -> None:
    i = 0
    while i < 2:
        n = i * 10 + 10
        i += 1
    for n in xs:  # tpyc: ok
        print("while_body in", n)
    print("while_body after", n)


# both arms of an `if` inside the body bind
def if_in_body(xs: list[int32]) -> None:
    for a in range(1, 3):
        if a > 1:
            n = a * 10
        else:
            n = 0
    for n in xs:  # tpyc: ok
        print("if_in_body in", n)
    print("if_in_body after", n)


# the loop's `else` clause binds first
def loop_else(xs: list[int32]) -> None:
    for a in range(1, 3):
        pass
    else:
        n = 20
    for n in xs:  # tpyc: ok
        print("loop_else in", n)
    print("loop_else after", n)


# the binding is two loops deep
def nested_loops(xs: list[int32]) -> None:
    for a in range(1, 3):
        for b in range(1, 2):
            n = a * 10 + b
    for n in xs:  # tpyc: ok
        print("nested_loops in", n)
    print("nested_loops after", n)


# first loop, head and read all inside one `if`
def inside_if(xs: list[int32]) -> None:
    if len(xs) < 100:
        for a in range(1, 3):
            n = a * 10
        for n in xs:  # tpyc: ok
            print("inside_if in", n)
        print("inside_if after", n)


# only the head sits inside an `if`
def head_in_if(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    if len(xs) < 100:
        for n in xs:  # tpyc: ok
            print("head_in_if in", n)
    print("head_in_if after", n)


# two later heads bind the same local
def two_heads(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    for n in xs:  # tpyc: ok
        print("two_heads in", n)
    for n in xs:  # tpyc: ok
        print("two_heads in2", n)
    print("two_heads after", n)


# BigInt element
def bigint_elem(xs: list[int32]) -> None:
    ys = [int(x) for x in xs]
    for a in range(1, 3):
        n = int(a) * 10
    for n in ys:  # tpyc: ok
        print("bigint_elem in", n)
    print("bigint_elem after", n)


# Optional element
def optional_elem(xs: list[int32]) -> None:
    ys: list[Optional[int32]] = []
    for x in xs:
        ys.append(x)
    for a in range(1, 3):
        n: Optional[int32] = a * 10
    for n in ys:  # tpyc: ok
        print("optional_elem in", n)
    print("optional_elem after", n)


class K:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    # method
    def method(self, xs: list[int32]) -> None:
        for a in range(1, 3):
            n = a * 10 + self.k
        for n in xs:  # tpyc: ok
            print("method in", n)
        print("method after", n)


# nested def
def nested_def(xs: list[int32]) -> None:
    def inner(ys: list[int32]) -> None:
        for a in range(1, 3):
            n = a * 10
        for n in ys:  # tpyc: ok
            print("nested_def in", n)
        print("nested_def after", n)
    inner(xs)


# generator, with a yield in the first loop
def gen(xs: list[int32]) -> Iterator[int32]:
    for a in range(1, 3):
        n = a * 10
        yield a
    for n in xs:  # tpyc: ok
        print("gen in", n)
    yield n


# generator, two sibling bodies (one suspends) then the head
def gen_two_siblings(xs: list[int32]) -> Iterator[int32]:
    for a in range(1, 3):
        n = a * 10
        yield a
    for b in range(1, 3):
        n = b * 100
    for n in xs:  # tpyc: ok
        print("gen_two_siblings in", n)
    yield n


# generator, no suspension in the loops, the body binds by tuple unpack
def gen_unpack_body(xs: list[int32]) -> Iterator[int32]:
    for a in range(1, 3):
        n, m = a * 10, a
    for n in xs:  # tpyc: ok
        print("gen_unpack_body in", n)
    yield n + m


# async, with an await in the first loop
async def async_body(xs: list[int32]) -> int32:
    for a in range(1, 3):
        n = a * 10
        await asyncio.sleep(0)
    for n in xs:  # tpyc: ok
        print("async_body in", n)
    return n


# async, two sibling bodies (one suspends) then the head
async def async_two_siblings(xs: list[int32]) -> int32:
    for a in range(1, 3):
        n = a * 10
        await asyncio.sleep(0)
    for b in range(1, 3):
        n = b * 100
    for n in xs:  # tpyc: ok
        print("async_two_siblings in", n)
    return n


# inverse: two sibling bodies bind before the head
def two_siblings(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    for b in range(1, 3):
        n = b * 100
    for n in xs:  # tpyc: ok
        print("two_siblings in", n)
    print("two_siblings after", n)


# inverse: no read after the head -- nothing can see the zero-trip value, so
# the head binds a loop-scoped const reference to each BigInt, no copy
def no_read_after(xs: list[int32]) -> None:
    ys = [int(x) for x in xs]
    for a in range(1, 3):
        n = int(a) * 10
        print("no_read_after body", n)
    for n in ys:  # tpyc: ok
        print("no_read_after in", n)


# inverse: a read between the loops promoted the local already
def read_between(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    print("read_between mid", n)
    for n in xs:  # tpyc: ok
        print("read_between in", n)
    print("read_between after", n)


# the only read is in the head loop's own `else` clause
def else_read(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    for n in xs:  # tpyc: ok
        print("else_read in", n)
    else:
        print("else_read else", n)


# the head sits in a `finally` (walked twice by liveness), the read after
# the `try`
def finally_head(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    try:
        print("finally_head try")
    finally:
        for n in xs:  # tpyc: ok
            print("finally_head in", n)
    print("finally_head after", n)


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


# inverse: a tuple with a reference element and no read after -- the head
# binds its own loop variable, and a write through it reaches the list
def ref_tuple_no_read_after(xs: list[int32]) -> None:
    cells = [(Cell(x), x) for x in xs]
    for a in range(1, 3):
        p = (Cell(a), a)
        print("ref_tuple_no_read_after body", p[0].v)
    for p in cells:  # tpyc: ok
        p[0].v += 100
    for c in cells:
        print("ref_tuple_no_read_after cell", c[0].v, c[1])


# inverse: str body and str head, no read after
def str_no_read_after(xs: list[int32]) -> None:
    names = [str(x) for x in xs]
    for a in range(1, 3):
        s = str(a)
        print("str_no_read_after body", s)
    for s in names:  # tpyc: ok
        print("str_no_read_after in", s)


# a binding (not a read) between the loops declared the local, which the
# head then rebinds
def bind_between(xs: list[int32]) -> None:
    for a in range(1, 3):
        n = a * 10
    n = 5
    for n in xs:  # tpyc: ok
        print("bind_between in", n)
    print("bind_between after", n)


# each section runs over an empty list (the zero-trip head) and a full one
def sections(xs: list[int32]) -> None:
    for_body(xs)
    while_body(xs)
    if_in_body(xs)
    loop_else(xs)
    nested_loops(xs)
    inside_if(xs)
    head_in_if(xs)
    two_heads(xs)
    bigint_elem(xs)
    optional_elem(xs)
    K().method(xs)
    nested_def(xs)
    for v in gen(xs):
        print("gen got", v)
    for v in gen_two_siblings(xs):
        print("gen_two_siblings got", v)
    for v in gen_unpack_body(xs):
        print("gen_unpack_body got", v)
    r = asyncio.run(async_body(xs))
    print("async_body after", r)
    r2 = asyncio.run(async_two_siblings(xs))
    print("async_two_siblings after", r2)
    two_siblings(xs)
    no_read_after(xs)
    read_between(xs)
    else_read(xs)
    finally_head(xs)
    ref_tuple_no_read_after(xs)
    str_no_read_after(xs)
    bind_between(xs)


def main() -> None:
    sections([])
    sections([7])


main()
