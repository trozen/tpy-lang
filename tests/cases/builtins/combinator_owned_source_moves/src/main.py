# A lazy combinator over a TEMPORARY owns it, and stays movable until its first
# pull, so another combinator (or a genexpr) may take it by value. __iter__ is
# user code: it runs once per source at the combinator call, in argument order,
# like CPython -- every __iter__ here prints. The one kind a combinator cannot
# keep movable, a temporary whose __iter__ returns a separate iterator, is bound
# to a local and borrowed where the position has a statement-level slot, and
# owned in place everywhere else -- so the shape compiles at every position.
from tpy import int32, Own, error_return, ReturnException
from typing import Iterator
import asyncio


class Cur:
    i: int32
    n: int32

    def __init__(self, n: int32) -> None:
        self.i = 0
        self.n = n

    def __iter__(self) -> "Cur":
        print("  Cur.__iter__", self.n)
        return self

    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


# inherits `__iter__`, which returns the base
class Sub(Cur):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


# same signature, but the base reference is a MEMBER it delegates to: the
# combinator must pull what `__iter__` returned, not the record
class Deleg(Cur):  # tpyc: warning(/hides 'Cur.__iter__'/)
    inner: Cur

    def __init__(self, n: int32) -> None:
        super().__init__(0)
        self.inner = Cur(n)

    def __iter__(self) -> Cur:
        return self.inner


# `__iter__` returns a FRESH instance of its own class: a separate iterator
class Rng:
    n: int32
    i: int32

    def __init__(self, n: int32) -> None:
        self.n = n
        self.i = 0

    def __iter__(self) -> Own["Rng"]:
        print("  Rng.__iter__", self.n)
        return Rng(self.n)

    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


class Noisy:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> Own[Cur]:
        print("  Noisy.__iter__", self.n)
        return Cur(self.n)


# the silent twin of Noisy, for positions nested inside a `print`
# (BUGS.md#print-arg-output-interleaves would reorder a trace line there)
class Quiet:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> Own[Cur]:
        return Cur(self.n)


# a separate-iterator record whose construction is observable
class Loud:
    n: int32

    def __init__(self, n: int32) -> None:
        print("  Loud.__init__", n)
        self.n = n

    def __iter__(self) -> Own[Cur]:
        return Cur(self.n)


class Failed(Exception, ReturnException):
    pass


class Scope:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Holder:
    n: int32
    quiet: Quiet
    count: int32

    # constructor position: no statement-level slot in a member init
    def __init__(self, n: int32) -> None:
        self.n = n
        self.quiet = Quiet(n)
        self.count = len(list(map(dbl, Quiet(n))))  # tpyc: ok

    def make(self) -> Own[Quiet]:
        return Quiet(self.n)

    @staticmethod
    def stat(n: int32) -> Own[Quiet]:
        return Quiet(n)

    # method position
    def total(self, xs: list[int32]) -> int32:
        t = 0
        for a, b in zip(Noisy(self.n), xs):  # tpyc: ok
            t += a * b
        return t


def gen() -> Iterator[int32]:
    yield 1
    yield 2
    yield 3


# a range-loop frame holds loop slots, so it is movable but not copyable
def ranged() -> Iterator[int32]:
    for i in range(3):
        yield i + 1


def make_list() -> Own[list[int32]]:
    return [4, 5, 6]


def make_set() -> Own[set[int32]]:
    return {5}


def add(a: int32, b: int32) -> int32:
    return a + b


def make_noisy(n: int32) -> Own[Noisy]:
    return Noisy(n)


def make_holder(n: int32) -> Own[Holder]:
    return Holder(n)


# async position
async def acount(xs: list[int32]) -> int32:
    return len(list(zip(Quiet(2), xs)))  # tpyc: ok


# @error_return body
@error_return(Failed)
def checked(xs: list[int32]) -> int32:
    n = len(list(zip(Quiet(2), xs)))  # tpyc: ok
    if n == 0:
        raise Failed()
    return n


# match arm
def by_match(k: int32, xs: list[int32]) -> int32:
    match k:
        case 1:
            return len(list(zip(Quiet(2), xs)))  # tpyc: ok
        case _:
            return 0


def dbl(v: int32) -> int32:
    return v * 2


def odd(v: int32) -> bool:
    return v % 2 != 0


# generator position, with no yield in the loop, so the holder is the state
# block's; the suspending twin (the frame holding the combinator and its
# temporary) is `combinator_temp_src` in generators/frame_for_source_once.
def pairs(xs: list[int32]) -> Iterator[int32]:
    t = 0
    for i, v in enumerate(Noisy(2)):  # tpyc: ok
        t += i * 10 + v
    yield t + xs[0]


# module-level statement
module_count = len(list(map(dbl, Quiet(2))))  # tpyc: ok


def main() -> None:
    xs = [10, 20, 30]

    # owning combinator moved into another owning combinator before its first pull
    for i, v in enumerate(map(dbl, make_list())):  # tpyc: ok
        print("enum_map", i, v)
    for a, b in zip(filter(odd, gen()), map(dbl, make_list())):  # tpyc: ok
        print("zip_filter_map", a, b)
    print("list_map_filter", list(map(dbl, filter(odd, make_list()))))  # tpyc: ok
    for v in reversed(list(map(dbl, make_list()))):  # tpyc: ok
        print("reversed_list_map", v)
    # generator-call temporaries are held by value and pulled directly, never
    # copied out of the combinator
    for a, b in zip(ranged(), gen()):  # tpyc: ok
        print("zip_frames", a, b)
    # an lvalue generator beside a temporary is advanced in place: the rest of
    # `rg` is what the zip left (it pulls `rg` first, then finds the list spent)
    rg = ranged()
    for a, b in zip(rg, [7]):  # tpyc: ok
        print("zip_lvalue_gen", a, b)
    print("rest", sum(rg))
    # a runtime view or set temporary is owned like a container
    d = {1: 7, 2: 8}
    for k, x in zip(d.keys(), xs):  # tpyc: ok
        print("zip_keys", k, x)
    vs = list(map(dbl, d.values()))  # tpyc: ok
    print("map_values", vs)
    ss = list(map(dbl, make_set()))  # tpyc: ok
    print("map_set", ss)
    # a genexpr over an owning combinator, moved into an owning combinator
    print("enum_genexpr_zip", sum(i + s for i, s in enumerate(a + b for a, b in zip(gen(), gen()))))  # tpyc: ok

    # separate-iterator temporary: a constructor call, at every combinator
    for i, v in enumerate(Noisy(2)):  # tpyc: ok
        print("enumerate", i, v)
    for a, b in zip(Noisy(2), xs):  # tpyc: ok
        print("zip", a, b)
    ys = list(map(dbl, Noisy(2)))  # tpyc: ok
    print("map", ys)
    zs = list(filter(odd, Noisy(3)))  # tpyc: ok
    print("filter", zs)
    # an Own-returning call, second argument
    for a, b in zip(xs, make_noisy(3)):  # tpyc: ok
        print("zip_call", a, b)
    # an lvalue of the same kind needs no temp
    n = Noisy(1)
    for a, b in zip(n, xs):  # tpyc: ok
        print("lvalue", a, b)
    # ... beside a temporary too: the iterator `__iter__` returned is held as
    # it came in, so a member the record delegates to and an inherited
    # `return self` are both advanced in place (a copy would leave them at 0)
    dl = Deleg(5)
    for a, b in zip(dl, make_list()):  # tpyc: ok
        print("lvalue_delegating", a, b)
    print("lvalue_delegating_left", dl.inner.i, dl.i)
    sb = Sub(5)
    for a, b in zip(sb, make_list()):  # tpyc: ok
        print("lvalue_inherited", a, b)
    print("lvalue_inherited_left", sb.i)
    # a self-iterator temporary is owned, and its __iter__ still runs
    for i, c in enumerate(Cur(2)):  # tpyc: ok
        print("self_iter", i, c)
    for a, b in zip(Sub(2), xs):  # tpyc: ok
        print("derived", a, b)
    for a, b in zip(Deleg(3), xs):  # tpyc: ok
        print("delegating", a, b)
    # an lvalue self-iterator is advanced in place by every combinator
    e1 = Cur(3)
    pairs_e = list(enumerate(e1))  # tpyc: ok
    print("borrow_enumerate", pairs_e, e1.i)
    e2 = Cur(3)
    doubled = list(map(dbl, e2))  # tpyc: ok
    print("borrow_map", doubled, e2.i)
    e3 = Cur(3)
    odds = list(filter(odd, e3))  # tpyc: ok
    print("borrow_filter", odds, e3.i)
    # an lvalue self-iterator beside a temporary is borrowed, not copied: the
    # caller's object carries the pulls (a copy would leave `cur.i` at 0)
    cur = Cur(5)
    for a, b in zip(cur, make_list()):  # tpyc: ok
        print("borrow_zip", a, b)
    print("borrow_zip_left", cur.i)
    cur2 = Cur(5)
    sums = list(map(add, cur2, make_list()))  # tpyc: ok
    print("borrow_map_n", sums, cur2.i)
    # two lvalue self-iterators: the borrowing zip advances both in place
    left = Cur(5)
    right = Cur(2)
    for a, b in zip(left, right):  # tpyc: ok
        print("borrow_both", a, b)
    print("borrow_both_left", left.i, right.i)
    # a fresh instance of its own class is a separate iterator
    for a, b in zip(Rng(2), xs):  # tpyc: ok
        print("fresh_self", a, b)
    # two observable sources: __iter__ runs left to right
    for a, b in zip(Noisy(2), Noisy(3)):  # tpyc: ok
        print("order", a, b)
    for a, b in zip(Noisy(1), Cur(1)):  # tpyc: ok
        print("order_mixed", a, b)
    # comprehension position
    prods = [a * b for a, b in zip(Noisy(2), xs)]  # tpyc: ok
    print("comprehension", prods)
    # conditional operand: the temp is built only on the taken arm
    taken = len(list(zip(Noisy(2), xs))) if len(xs) > 1 else 0  # tpyc: ok
    print("cond_taken", taken)
    skipped = len(list(zip(Loud(9), xs))) if len(xs) > 9 else 0  # tpyc: ok
    print("cond_skipped", skipped)
    built = len(list(zip(Loud(2), xs))) if len(xs) > 1 else 0  # tpyc: ok
    print("cond_built", built)
    # loop head: re-evaluated, so rebuilt, every iteration
    rounds = 0
    while rounds < len(list(map(dbl, Noisy(2)))):  # tpyc: ok
        rounds += 1
    print("while_head", rounds)

    # positions with no statement-level slot: the temporary is owned in place
    print("nested_call", list(map(dbl, Quiet(2))))  # tpyc: ok
    print("nested_call_multi", "p", len(list(zip(Quiet(2), xs))))  # tpyc: ok
    print("genexpr_source", sum(a * b for a, b in zip(Quiet(2), xs)))  # tpyc: ok
    if len(xs) > 1 and len(list(map(dbl, Quiet(2)))) == 2:  # tpyc: ok
        print("and_operand")
    table = {1: len(list(zip(Quiet(2), xs)))}  # tpyc: ok
    print("dict_elem", table[1])
    print("module_level", module_count)
    print("async", asyncio.run(acount(xs)))
    # sources that are not a constructor or free-function call keep the owned form
    hold = Holder(2)
    print("ctor_init", hold.count)
    for a, b in zip(hold.make(), xs):  # tpyc: ok
        print("method_source", a, b)
    for a, b in zip(Holder.stat(2), xs):  # tpyc: ok
        print("static_source", a, b)
    for a, b in zip(make_holder(2).quiet, xs):  # tpyc: ok
        print("field_of_temp", a, b)

    # closure position
    def inner_total() -> int32:
        return len(list(zip(Quiet(2), xs)))  # tpyc: ok
    print("closure", inner_total())
    # try / finally
    try:
        tf = len(list(zip(Quiet(2), xs)))  # tpyc: ok
        print("try", tf)
    finally:
        print("finally", len(list(map(dbl, Quiet(1)))))  # tpyc: ok
    # context-manager body
    with Scope() as one:
        print("with", one + len(list(zip(Quiet(2), xs))))  # tpyc: ok
    print("match", by_match(1, xs))
    try:
        print("error_return", checked(xs))
    except Failed:
        print("error_return failed")

    # bound first: `print` streams its arguments as it evaluates them
    # (BUGS.md#print-arg-output-interleaves), which would reorder the trace.
    m = Holder(2).total(xs)
    print("method", m)
    g = list(pairs(xs))
    print("generator", g)


main()
