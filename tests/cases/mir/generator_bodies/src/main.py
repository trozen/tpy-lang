# MIR lowers a generator's body from its published frame and warns in it as in a plain
# function; the frame features it does not model yet are refused by name.
from typing import Iterator

from tpy import int32


def bound(events: list[int], tag: int, value: int) -> int:
    events.append(tag)
    return value


# gen_free: a free generator over int32 / bool locals and a list parameter, which it
# mutates between yields; the caller sees the change
def gen_free(xs: list[int32], flag: bool) -> Iterator[int32]:  # tpyc: mir(covered)
    total = 0
    for x in xs:
        total += x
    big = total > 3
    yield total
    if big and flag:
        yield len(xs)
    xs.append(total)
    yield 0


class Counter:
    count: int32

    def __init__(self, count: int32) -> None:
        self.count = count

    # gen_method: a method generator reading and writing its receiver across yields
    def ticks(self, step: int32) -> Iterator[int32]:  # tpyc: mir(covered)
        while self.count > 0:
            self.count -= step
            yield self.count


# gen_while: a `while` loop around a `yield`, a local carried across the suspension
def gen_while(n: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    i = 0
    while i < n:
        yield i * i
        i += 1


# gen_alias: the argument-alias warning of a plain function, at a `yield`
def gen_alias(xs: list[int]) -> Iterator[int]:
    yield bound(xs, 1, xs[0])  # tpyc: warning(/'xs\[0\]' points into 'xs', which 'bound' modifies/)


# gen_last_use: a local yielded at its last use moves into the yield, which reads it as a copy
def scalar_local() -> Iterator[int32]:  # tpyc: mir(covered)
    x = 1
    yield x


def reused_local() -> Iterator[int32]:  # tpyc: mir(covered)
    x = 2
    yield x
    yield x


def conditional_local(flag: bool) -> Iterator[int32]:  # tpyc: mir(covered)
    if flag:
        x = 3
    else:
        x = 4
    yield x


def reassigned_local() -> Iterator[int32]:  # tpyc: mir(covered)
    x = 5
    yield x
    x = 6
    yield x


# gen_last_use: the body a last-use yield used to keep MIR out of warns at its hazard
def tagged(events: list[str], value: str) -> int32:
    events.append("new")
    return len(value)


def last_use_hazard(xs: list[str]) -> Iterator[int32]:  # tpyc: mir(conflict /argument_alias/)
    yield tagged(xs, xs[0])  # tpyc: warning(/'xs\[0\]' points into 'xs', which 'tagged' modifies/)
    x = 7
    yield x


# gen_owned: a `str` parameter's frame copy yielded as the generator's owned leaf
def gen_owned(s: str) -> Iterator[str]:  # tpyc: mir(covered)
    yield s


# not covered: an async body
async def not_async(n: int32) -> int32:  # tpyc: mir(uncovered /^async body$/)
    return n


# not covered: a `for` loop whose body suspends
def not_for(xs: list[int32]) -> Iterator[int32]:  # tpyc: mir(uncovered /^loop across a suspension$/)
    for x in xs:
        yield x


# not covered: a `try` whose body suspends
def not_try(n: int32) -> Iterator[int32]:  # tpyc: mir(uncovered /^try across a suspension$/)
    try:
        yield n
    finally:
        print("not_try: finally")


def main() -> None:
    xs = [1, 2, 3]
    for v in gen_free(xs, True):
        print("gen_free:", v)
    print("gen_free: xs", len(xs), xs[3])

    c = Counter(5)
    for t in c.ticks(2):
        print("gen_method:", t)
    print("gen_method: count", c.count)

    for w in gen_while(4):
        print("gen_while:", w)

    # Spare capacity: the append inside `bound` keeps 'xs[0]' where it is, so this run reads
    # what CPython reads; the warning is the subject.
    ys = [5, 6]
    ys.pop()
    for _ in gen_alias(ys):
        print("gen_alias: len", len(ys))

    for a in scalar_local():
        print("gen_last_use: scalar", a)
    for b in reused_local():
        print("gen_last_use: reused", b)
    for flag in [True, False]:
        for d in conditional_local(flag):
            print("gen_last_use: conditional", d)
    for e in reassigned_local():
        print("gen_last_use: reassigned", e)
    zs = ["p", "q"]
    zs.pop()
    for _ in last_use_hazard(zs):
        print("gen_last_use: hazard len", len(zs))

    for o in gen_owned("hi"):
        print("gen_owned:", o)

    for z in not_for([7]):
        print("not_for:", z)
    for y in not_try(8):
        print("not_try:", y)
    # not covered: a generator expression (its body has no def line to pin; the unit tests pin it)
    print("not_genexpr:", sum(k * 2 for k in [1, 2, 3]))


main()
