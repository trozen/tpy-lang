# An `Iterable[Own[T]]` parameter is consumed by its first traversal: every shape
# here traverses it once per path and compiles (LANGUAGE_FEATURES "An owning iterable ...").
import asyncio
from typing import Iterable, Iterator, Protocol
from tpy import Own, Spannable, int32, readonly, span


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> Own[list[R]]:
    return [R(1), R(2), R(3)]


# A generator rather than `mk()`: a fresh list at the union parameter is refused
# today (BUGS.md#fresh-arg-at-protocol-union-param-rejects).
def make_steps() -> Iterator[Own[R]]:
    for n in range(1, 4):
        yield R(n)


# free function: one `for` over the parameter
def total(it: Iterable[Own[R]]) -> int32:
    n = 0
    for r in it:  # tpyc: ok
        n += r.n
    return n


# method: one `for`, each element kept
class Acc:
    rows: list[R]

    def __init__(self) -> None:
        self.rows = []

    def take_all(self, it: Iterable[Own[R]]) -> int32:
        for r in it:  # tpyc: ok
            self.rows.append(r)
        return len(self.rows)


# constructor: one hand-over to `extend`
class Bag:
    rows: list[R]

    def __init__(self, it: Iterable[Own[R]]) -> None:
        self.rows = []
        self.rows.extend(it)  # tpyc: ok


# generator: one `for` across the suspensions
def gen_ns(it: Iterable[Own[R]]) -> Iterator[int32]:
    for r in it:  # tpyc: ok
        yield r.n


# async: one `for` with an await in its body
async def async_total(it: Iterable[Own[R]]) -> int32:
    n = 0
    for r in it:  # tpyc: ok
        await asyncio.sleep(0)
        n += r.n
    return n


# guard clause: each path reads the parameter once
def guarded(it: Iterable[Own[R]], fast: bool) -> int32:
    if fast:
        return total(list(it))  # tpyc: ok
    n = 0
    for r in it:  # tpyc: ok
        n += 1
    return n


# union arm: `isinstance` does not traverse; only the narrowed arm's loop does
def union_arm(items: Spannable[R] | Iterable[Own[R]]) -> int32:
    if isinstance(items, Spannable):
        return len(span(items))
    n = 0
    for r in items:  # tpyc: ok
        n += r.n
    return n


# two passes over a copy
def twice(it: Iterable[Own[R]]) -> int32:
    xs = list(it)  # tpyc: ok
    n = 0
    for r in xs:
        n += r.n
    for r in xs:
        n += r.n
    return n


# forwarding hands the parameter over to another owning parameter
def forward(it: Iterable[Own[R]]) -> int32:
    return total(it)  # tpyc: ok


# an alias carries the one traversal
def via_alias(it: Iterable[Own[R]]) -> int32:
    b = it  # tpyc: ok
    return total(b)


# each traversal hook once: an `in` test, `+=`, a comprehension source (a genexpr
# over an owning source does not lower yet: BUGS.md#genexpr-owned-source-rejects)
def in_once(it: Iterable[Own[int32]]) -> bool:
    return 2 in it  # tpyc: ok


def iadd_once(it: Iterable[Own[R]]) -> int32:
    out: list[R] = []
    out += it  # tpyc: ok
    return len(out)


def comp_once(it: Iterable[Own[R]]) -> int32:
    return len([r.n for r in it])  # tpyc: ok


# value-type elements: the rule is the signature's
def ints(it: Iterable[Own[int32]]) -> int32:
    n = 0
    for v in it:  # tpyc: ok
        n += v
    return n


# the arms of a `match` each read the parameter once
def by_mode(it: Iterable[Own[R]], mode: int32) -> int32:
    match mode:
        case 0:
            return total(it)  # tpyc: ok
        case _:
            return len(list(it))  # tpyc: ok


# a consumption on a returning branch reaches the `finally`, not the code after it
def finally_guard(it: Iterable[Own[R]], fast: bool) -> int32:
    try:
        if fast:
            return len(list(it))
    finally:
        pass
    n = 0
    for r in it:  # tpyc: ok
        n += r.n
    return n


# a traversal in a loop body that returns through a `finally` never runs again
def loop_return(it: Iterable[Own[R]]) -> int32:
    n = 0
    for _ in range(2):
        try:
            for r in it:  # tpyc: ok
                n += r.n
            return n
        finally:
            n += 100
    return n


# a traversal followed by a `break` runs once
def traverse_then_break(it: Iterable[Own[R]], k: int32) -> int32:
    n = 0
    for i in range(k):
        if i == 1:
            n += total(it)  # tpyc: ok
            break
    return n


# a `break` in a `finally` leaves the loop too: the traversal never runs again
def finally_may_break(it: Iterable[Own[R]], stop: bool) -> int32:
    n = 0
    for _ in range(2):
        try:
            n += len(list(it))  # tpyc: ok
            return n
        finally:
            if stop:
                break
    return n


class Quiet:
    def __enter__(self) -> None:
        pass

    def __exit__(self, et, ev, tb) -> bool:
        return True


# one traversal in a `with` body
def with_once(it: Iterable[Own[R]]) -> int32:
    n = 0
    with Quiet():
        for r in it:  # tpyc: ok
            n += r.n
    return n


def pairs() -> Iterator[Own[tuple[R, int32]]]:
    yield (R(1), 10)
    yield (R(2), 20)


# a whole-`Own` tuple step (fed a generator: a list of tuples does not conform
# to the protocol today; an unpacking head over a protocol parameter is refused
# at code generation, LANGUAGE_FEATURES "Unpacking at a `for` head")
def tuple_steps(it: Iterable[Own[tuple[R, int32]]]) -> int32:
    n = 0
    for p in it:  # tpyc: ok
        n += p[0].n + p[1]
    return n



class Sizes[T](Protocol):
    @readonly
    def size(self) -> int32: ...


class Sized3:
    def __init__(self, n: int32) -> None:
        self.n = n

    @readonly
    def size(self) -> int32:
        return self.n


# a user protocol with an `Own` type argument keeps its forwarding parameter
def count(b: Sizes[Own[R]]) -> int32:
    return b.size()  # tpyc: ok


def main() -> None:
    print("free", total(mk()))
    acc = Acc()
    print("method", acc.take_all(mk()), acc.rows[2].n)
    rows = mk()
    bag = Bag(rows)
    print("ctor", len(bag.rows), bag.rows[0].n)
    print("gen", list(gen_ns(mk())))
    print("async", asyncio.run(async_total(mk())))
    print("guard", guarded(mk(), True), guarded(mk(), False))
    steps = make_steps()
    print("union", union_arm(steps))
    print("twice", twice(mk()))
    print("forward", forward(mk()))
    print("alias", via_alias(mk()))
    print("hooks", in_once([1, 2]), iadd_once(mk()), comp_once(mk()))
    print("ints", ints([1, 2, 3]))
    print("match", by_mode(mk(), 0), by_mode(mk(), 1))
    print("finally", finally_guard(mk(), True), finally_guard(mk(), False))
    print("loop_return", loop_return(mk()))
    print("break", traverse_then_break(mk(), 3), traverse_then_break(mk(), 1))
    print("finally_break", finally_may_break(mk(), True), finally_may_break(mk(), False))
    print("with", with_once(mk()))
    print("tuple", tuple_steps(pairs()))
    print("protocol", count(Sized3(3)))


main()
