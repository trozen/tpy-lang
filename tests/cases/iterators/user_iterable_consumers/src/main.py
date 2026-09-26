# A user iterable (a class with `__iter__`) or Iterable/Iterator param at every
# consumer that loops over an iterable: builtins, constructors, comprehensions.
from __future__ import annotations
import asyncio
from typing import Iterable, Iterator
from tpy import Own, int32, readonly


class Bag:
    items: list[str]

    def __init__(self) -> None:
        self.items = ["b", "a"]

    def __iter__(self) -> Iterator[str]:
        return iter(self.items)


class Cnt:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> Cnt:
        return self

    def __next__(self) -> int32:
        if self.n == 0:
            raise StopIteration
        self.n -= 1
        return self.n


class GBag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [3, 1]

    def __iter__(self) -> Iterator[int32]:
        for x in self.items:
            yield x


class Rec:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self) -> int32:
        self.v += 100
        return self.v


class RBag:
    items: list[Rec]

    def __init__(self) -> None:
        self.items = [Rec(1), Rec(2)]

    def __iter__(self) -> Iterator[Rec]:
        return iter(self.items)


class PBag:
    items: list[tuple[str, int32]]

    def __init__(self) -> None:
        self.items = [("a", 1), ("b", 2)]

    def __iter__(self) -> Iterator[tuple[str, int32]]:
        return iter(self.items)


class Src:
    it: Cnt

    def __init__(self) -> None:
        self.it = Cnt(3)

    # Hands back a STORED iterator: a loop advances the member itself.
    def __iter__(self) -> Cnt:
        return self.it


class Tx:
    started: int32
    names: list[str]

    def __init__(self) -> None:
        self.started = 0
        self.names = ["t", "u"]

    # Methods named begin/end are not a C++ range: iteration takes `__iter__`.
    def begin(self) -> None:
        self.started += 1

    def end(self) -> None:
        self.started -= 1

    def __iter__(self) -> Iterator[str]:
        return iter(self.names)


class Window:
    lo: int32 | None
    names: list[str]

    def __init__(self) -> None:
        self.lo = 1
        self.names = ["p", "q"]

    # begin/end returning an Optional are not an iterator pair either.
    def begin(self) -> int32 | None:
        return self.lo

    def end(self) -> int32 | None:
        return None

    def __iter__(self) -> Iterator[str]:
        return iter(self.names)


class Ints:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [104, 105]

    def __iter__(self) -> Iterator[int32]:
        return iter(self.items)


class Box[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)


class Holder:
    bag: Bag

    def __init__(self) -> None:
        self.bag = Bag()

    def values(self) -> Own[list[str]]:
        # method: a comprehension over a field
        return [s + "?" for s in self.bag]  # tpyc: ok

    def any_a(self) -> bool:
        # method: a comprehension over a field, handed to any() (a generator
        # expression is rejected: BUGS.md#genexpr-over-user-iterable)
        return any([s == "a" for s in self.bag])  # tpyc: ok

    def listed(self) -> Own[list[str]]:
        # method: list() over a field
        return list(self.bag)

    def make(self) -> Own[Bag]:
        return Bag()


class Letters:
    chars: list[str]

    def __init__(self, chars: Own[list[str]]) -> None:
        self.chars = chars

    def __iter__(self) -> Iterator[str]:
        for c in self.chars:
            yield c

    def upper(self) -> Own[list[str]]:
        # method: a comprehension over `self`
        return [c.upper() for c in self]  # tpyc: ok

    def listed(self) -> Own[list[str]]:
        # method: list() over `self`
        return list(self)  # tpyc: ok


def mk() -> Own[Bag]:
    return Bag()


def words() -> Iterator[str]:
    yield "q"
    yield "a"


def show(xs: Iterable[str]) -> None:
    # an Iterable param handed to list()
    print("show", list(xs))


def proto_comp(xs: Iterable[int32]) -> Own[list[int32]]:
    # an Iterable param handed a stored-iterator record: the member advances
    return [x for x in xs]  # tpyc: ok


def ups(xs: Iterable[str]) -> Own[list[str]]:
    # an Iterable param as a comprehension source
    return [s.upper() for s in xs]  # tpyc: ok


def drain(it: Iterator[str]) -> Own[list[str]]:
    # an Iterator param as a comprehension source
    return [s for s in it]  # tpyc: ok


def param_comp(rb: RBag) -> Own[list[int32]]:
    # free function: a comprehension over a record param
    return [r.v * 10 for r in rb]  # tpyc: ok


def ro_comp(b: readonly[Bag]) -> Own[list[str]]:
    # a readonly receiver
    return [s + s for s in b]  # tpyc: ok


def gen_body(b: Bag) -> Iterator[int32]:
    # generator body: a comprehension evaluated inside a resumable frame
    vs = [len(s) for s in b]  # tpyc: ok
    yield len(vs)
    yield sum([len(s) for s in b])  # a comprehension, not a genexpr: BUGS.md#genexpr-over-user-iterable


def gen_protocol(xs: Iterable[str]) -> Iterator[int32]:
    # generator body: an Iterable param as the source
    yield len([s for s in xs])  # tpyc: ok
    yield sum([len(s) for s in xs])  # a comprehension, not a genexpr: BUGS.md#genexpr-over-user-iterable


async def async_body(b: Bag) -> int32:
    # async body
    await asyncio.sleep(0)
    return len([s for s in b])  # tpyc: ok


def main() -> None:
    b = Bag()
    # builtins that loop over an iterable
    print("builtins", sorted(b), any(b), all(b))  # tpyc: ok
    print("sorted_key", sorted(b, key=lambda s: 0 if s == "b" else 1))  # tpyc: ok
    g = GBag()
    print("generator_iter", sorted(g), sum(g))  # tpyc: ok
    c = Cnt(3)
    # a self-iterator is drained in place, as in CPython
    print("self_iter", sorted(c), c.n, sum(Cnt(4)))  # tpyc: ok
    # list() / set() / dict() over a user iterable
    print("construct", list(b), sorted(set(b)), list(mk()), list(Cnt(2)))  # tpyc: ok
    print("dict", dict(PBag()))  # tpyc: ok
    show(b)

    # comprehensions over a local
    print("comp_local", [s.upper() for s in b], sorted({s for s in b}),  # tpyc: ok
          {s: len(s) for s in b})
    # sum([...]) below stands in for a genexpr: BUGS.md#genexpr-over-user-iterable
    print("self_iter_comp", [x for x in Cnt(3)], sum([x for x in Cnt(4)]))
    print("generator_iter_comp", [x * 2 for x in g], sum([x for x in g]))
    # an rvalue source
    print("rvalue", [s for s in mk()], sorted({s for s in mk()}))  # tpyc: ok
    print("param", param_comp(RBag()))

    # the loop variable aliases the element the iterable lends
    rb = RBag()
    print("mutate", [r.bump() for r in rb], [r.v for r in rb])

    h = Holder()
    print("method", h.values(), h.any_a(), h.listed())
    # an OWNED method-call result is owned before the loop
    print("owned_method_source", [s for s in h.make()])  # tpyc: ok
    print("self_source", Letters(["h", "i"]).upper(),
          Letters(["o", "k"]).listed())
    print("readonly", ro_comp(b))
    for n in gen_body(b):
        print("generator", n)
    for n in gen_protocol(["x", "yy"]):
        print("generator_protocol", n)
    print("async", asyncio.run(async_body(b)))

    def closure() -> Own[list[str]]:
        # closure: a nested def capturing the iterable
        return [s for s in b]  # tpyc: ok
    print("closure", closure())

    bx = Box([1, 2])
    print("generic", [x + 1 for x in bx], sum([x for x in bx]), sorted(bx))  # tpyc: ok
    print("protocol", ups(["b", "a"]), ups(Bag()), ups(words()))
    print("iterator_param", drain(words()), drain(iter(["m", "n"])))

    # statement-scoped consumers advance a stored member iterator in place
    s4 = Src()
    print("member_stmt", [x for x in s4], s4.it.n, list(s4), sum(s4))  # tpyc: ok
    s5 = Src()
    print("member_proto", proto_comp(s5), s5.it.n)
    # a reassigned local
    c2 = Cnt(3)
    print("reassigned", [x for x in c2], c2.n)
    c2 = Cnt(2)
    print("reassigned", [x for x in c2], sorted(set(Cnt(1))), sum([x for x in c2]))  # tpyc: ok
    c2 = Cnt(2)
    print("reassigned_set", sorted(set(c2)), c2.n)  # tpyc: ok
    t = Tx()
    t.begin()
    print("begin_end_methods", list(t), ",".join(t), t.started)  # tpyc: ok
    w = Window()
    print("begin_end_optional", list(w), [x + "!" for x in w], w.begin())  # tpyc: ok
    # bytes / bytearray / bytearray.extend over a user iterable of ints
    ib = Ints()
    ba = bytearray(ib)  # tpyc: ok
    ba.extend(ib)  # tpyc: ok
    print("bytes", bytes(ib), ba)  # tpyc: ok
    # slice and stepped-slice assignment from a user iterable
    xs = ["x", "y"]
    xs.append("z")
    xs[0:1] = b  # tpyc: ok
    print("slice", xs)
    xs[::2] = Bag()  # tpyc: ok
    print("stepped_slice", xs)


main()
mb = Bag()
# module level
print("module", [s for s in mb], len({s for s in mb}))
