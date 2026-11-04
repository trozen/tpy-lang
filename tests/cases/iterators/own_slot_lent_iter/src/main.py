# An owning element slot over a user iterator stays silent where the copy is
# declared or the elements are handed over, matching CPython.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, Ptr, int32, copy, copy_iter, nocopy

G = [[1], [2]]


class View:
    def __iter__(self) -> Iterator[list[int32]]:
        for x in G:
            yield x


def fresh() -> Iterator[Own[list[int32]]]:
    yield [1]
    yield [2]


class Ints:
    def __iter__(self) -> Iterator[int32]:
        yield 1
        yield 2


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def push(self, x: Own[T]) -> None:
        self.items.append(x)

    # lends its own field's elements: a pop under `Iterator[T]` would be a
    # fresh value, which only `Iterator[Own[T]]` hands over
    def __iter__(self) -> Iterator[T]:
        for x in self.items:
            yield x

    def drain(self) -> Iterator[Own[T]]:
        while len(self.items) > 0:
            yield self.items.pop()


class SubBag(Bag[list[int32]]):
    pass


class OwnCursor:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> "OwnCursor":
        return self

    def __next__(self) -> Own[list[int32]]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return [self.n]


class OwnIt:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __next__(self) -> Own[list[int32]]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return [self.n]


class OwnSeparate:
    def __iter__(self) -> Own[OwnIt]:
        return OwnIt()


def drain[T](xs: list[T]) -> Iterator[Own[T]]:
    while len(xs) > 0:
        yield xs.pop()


# lends its parameter's elements
def each_lent[T](xs: list[T]) -> Iterator[T]:
    for x in xs:
        yield x


def clones_own[T](src: Iterable[T]) -> Iterator[Own[T]]:
    for x in src:
        yield copy(x)


def from_param(xs: list[list[int32]]) -> Iterator[list[int32]]:
    for x in xs:
        yield x


class Rows:
    items: list[list[int32]]

    def __init__(self) -> None:
        self.items = [[1]]


R = Rows()


class PtrView:
    p: Ptr[Rows]

    def __init__(self, p: Ptr[Rows]) -> None:
        self.p = p

    def __iter__(self) -> Iterator[Ptr[Rows]]:
        yield self.p


def bump(tag: str, source: Iterable[Own[list[int32]]]) -> None:
    for x in source:
        x.append(99)
        print(tag, x)


def total(source: Iterable[Own[int32]]) -> int32:
    n = 0
    for x in source:
        n += x
    return n


def take_ptrs(source: Iterable[Own[Ptr[Rows]]]) -> None:
    for p in source:
        p.items.append([3])


def keep(x: list[int32]) -> bool:
    return True


@nocopy
class Tok:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def fresh_toks() -> Iterator[Own[Tok]]:
    yield Tok(1)
    yield Tok(2)


class TokCursor:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> "TokCursor":
        return self

    def __next__(self) -> Own[Tok]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return Tok(self.n)


def bump_toks(tag: str, source: Iterable[Own[Tok]]) -> None:
    for t in source:
        t.v += 99
        print(tag, t.v)


def nocopy_handover() -> None:
    # a @nocopy element proves the handover is a move: a copy would not compile
    bump_toks("nocopy Own generator", fresh_toks())  # tpyc: ok
    bump_toks("nocopy Own __next__", TokCursor())  # tpyc: ok
    kept = list(fresh_toks())  # tpyc: ok
    print("nocopy list()", kept[0].v, kept[1].v)


def free_function() -> None:
    # free function
    bump("free copy_iter", copy_iter(View()))  # tpyc: ok
    print("free copy_iter G", G)
    bump("free Own generator", fresh())  # tpyc: ok
    nl: list[list[int32]] = [[5]]
    bump("free copy() of a list", copy(nl))  # tpyc: ok
    print("free copy() of a list nl", nl)
    print("free int32 iterator", total(Ints()))  # tpyc: ok
    # a generic step lending its field copies at an owning slot, as the
    # concrete twin's does
    bag = Bag[list[int32]]()
    bag.push([7])
    bump("free generic step", bag)  # tpyc: warning(/copies list\[int32\] elements/)
    # an Own type argument on a generic step still lends the field's
    # elements, so they copy (handing them over needs a popping
    # `Iterator[Own[T]]` step, which an `Iterable` slot refuses today:
    # BUGS.md#own-yield-generator-refused-at-iterable-slot)
    ob = Bag[Own[list[int32]]]()
    e: list[int32] = [8]
    ob.push(e)
    bump("free Own type arg", ob)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free Own type arg ob", len(ob.items))
    # a generic generator declared to hand over its copies
    bump("free clones_own", clones_own(View()))  # tpyc: ok
    print("free clones_own G", G)
    cl: list[list[int32]] = [[15]]
    # `Iterator[T]` declares a lending step: a named argument's elements copy
    # (the source read-back is the divergence case's "free copying generic")
    bump("free clones named", each_lent(cl))  # tpyc: warning(/copies list\[int32\] elements/)
    # a subclass binding an inherited generic __iter__'s T lends as its base
    sb = SubBag()
    sb.push([14])
    bump("free SubBag", sb)  # tpyc: warning(/copies list\[int32\] elements/)
    # a __next__ declared Own hands each element over
    bump("free Own __next__", OwnCursor())  # tpyc: ok
    bump("free Own separate __next__", OwnSeparate())  # tpyc: ok
    # a user iterator lending a Ptr: the pointer is the value handed over
    take_ptrs(PtrView(R))  # tpyc: ok
    print("free Ptr step", len(R.items))
    # generic producers over an open T declared to hand their pops over
    d = [[9]]
    bump("free drain", drain(d))  # tpyc: ok
    print("free drain d", d)
    bd = Bag[list[int32]]()
    bd.push([10])
    bump("free Bag.drain", bd.drain())  # tpyc: ok
    # a lending generic generator under a lazy combinator copies at the slot
    fd = [[11]]
    bump("free filter drain", filter(keep, each_lent(fd)))  # tpyc: warning(/copies list\[int32\] elements/)
    # a genexpr warned already; a generator over its parameter warns for a
    # temporary argument too: the copy is unobservable, but the signature
    # cannot tell its parameter from a global (the declared-signature rule)
    bump("free from_param", from_param([[12]]))  # tpyc: warning(/copies list\[int32\] elements/)
    gl: list[list[int32]] = [[13]]
    bump("free genexpr", (x for x in gl))  # tpyc: warning(/copies list\[int32\] elements/)


class Driver:
    def run(self) -> None:
        # method body
        bump("method copy_iter", copy_iter(View()))  # tpyc: ok
        bump("method Own generator", fresh())  # tpyc: ok
        print("method G", G)


def gen_body() -> Iterator[int32]:
    # generator body
    bump("generator copy_iter", copy_iter(View()))  # tpyc: ok
    bump("generator Own generator", fresh())  # tpyc: ok
    print("generator G", G)
    yield 1


async def async_body() -> None:
    # async body
    bump("async copy_iter", copy_iter(View()))  # tpyc: ok
    await asyncio.sleep(0)
    bump("async Own generator", fresh())  # tpyc: ok
    print("async G", G)


def main() -> None:
    free_function()
    nocopy_handover()
    Driver().run()
    for _ in gen_body():
        pass
    asyncio.run(async_body())


main()
