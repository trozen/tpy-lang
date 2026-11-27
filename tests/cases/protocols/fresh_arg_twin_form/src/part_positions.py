# The same fresh arguments at a protocol parameter in the other positions:
# method, constructor member-init, super().__init__, generator body, generator
# with-head and comprehension. Each section's concrete twin sits beside it.
from typing import Iterable, Iterator
from tpy import int32
from fixtures import (Acc, Bag, bump_twice, bump_twice_c, total, total_b,
                      total_l)


class Calc:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def run(self, k: int32) -> None:
        # method body: range / comprehension / record; twins total_l / total_b
        print("method range", self.base + total(range(k)))  # tpyc: ok
        print("method comp", total([i + self.base for i in range(k)]),  # tpyc: ok
              total_l([i + self.base for i in range(k)]))
        print("method record", total(Bag(k)), total_b(Bag(k)))  # tpyc: ok
        print("method proto", bump_twice(Acc(self.base)),  # tpyc: ok
              bump_twice_c(Acc(self.base)))


class Holder:
    a: int32
    b: int32
    c: int32
    d: int32
    e: int32

    def __init__(self, k: int32) -> None:
        # member-init: the fresh argument stays in the init list (twins as in main.py)
        self.a = total(range(4))  # tpyc: ok
        self.b = total([i for i in range(k)])  # tpyc: ok
        self.c = total(Bag(k))  # tpyc: ok
        self.d = bump_twice(Acc(k))  # tpyc: ok
        # a conditional of a fresh range at member-init
        self.e = total(range(k)) if k > 2 else 0  # tpyc: ok


class Base:
    acc: int32

    def __init__(self, it: Iterable[int32]) -> None:
        s = 0
        for x in it:
            s += x
        self.acc = s


class FromRange(Base):
    def __init__(self) -> None:
        # super().__init__ with a fresh range at the base's protocol parameter
        super().__init__(range(4))  # tpyc: ok


class FromComp(Base):
    def __init__(self, k: int32) -> None:
        super().__init__([i * 10 for i in range(k)])  # tpyc: ok


class Count:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __enter__(self) -> int32:
        return self.v

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def gen_body(k: int32) -> Iterator[int32]:
    # generator body: each yield follows a fresh argument at the protocol parameter
    yield total(range(k))  # tpyc: ok
    yield total([i * 2 for i in range(k)])  # tpyc: ok
    yield total(Bag(k))  # tpyc: ok
    yield bump_twice(Acc(k))  # tpyc: ok


def gen_with(xs: list[int32]) -> Iterator[int32]:
    # generator with-head: the fresh argument is seated as at the twin's head
    with Count(total([n for n in xs])) as n:  # tpyc: ok
        yield n
    with Count(total_l([n for n in xs])) as m:
        yield m
    with Count(total(range(4))) as r:  # tpyc: ok
        yield r


def comp_pos(k: int32) -> None:
    # comprehension element: a fresh argument per iteration
    print("comp range", [total(range(i)) for i in range(k)])  # tpyc: ok
    print("comp list", [total([i, i]) for i in range(k)],  # tpyc: ok
          [total_l([i, i]) for i in range(k)])
    print("comp record", [bump_twice(Acc(i)) for i in range(k)])  # tpyc: ok


def main() -> None:
    Calc(5).run(3)
    h = Holder(3)
    print("init", h.a, h.b, h.c, h.d, h.e)
    print("super range", FromRange().acc)
    print("super comp", FromComp(3).acc)
    print("gen body", list(gen_body(3)))
    print("gen with", list(gen_with([1, 2, 3])))
    comp_pos(4)
