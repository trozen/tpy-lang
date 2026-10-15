# `x in h` runs as the call `h.__contains__(x)`: same winner, needle conversion
# and argument temps (winners first; twin: calls/dispatch_rank_declared_shape_order).
import asyncio
from typing import Iterator
from tpy import Own, dispatch, int8, int16, int32, nocopy


class B:
    def __init__(self) -> None:
        pass

    @dispatch
    def __contains__(self, x: int16) -> bool:
        print("B.i16")
        return True

    @dispatch
    def __contains__(self, x: int32) -> bool:
        print("B.i32")
        return False


class BLit:
    def __init__(self) -> None:
        pass

    @dispatch
    def __contains__(self, x: int32) -> bool:
        print("BLit.i32")
        return True

    @dispatch
    def __contains__(self, x: int16) -> bool:
        print("BLit.i16")
        return False


class G[T]:
    def __init__(self) -> None:
        pass

    @dispatch
    def __contains__(self, x: int16) -> bool:
        print("G.i16")
        return True

    @dispatch
    def __contains__(self, x: int32) -> bool:
        print("G.i32")
        return False


class Words:
    def __init__(self) -> None:
        pass

    @dispatch
    def __contains__(self, x: str) -> bool:
        print("Words.str")
        return True

    @dispatch
    def __contains__(self, x: bool) -> bool:
        print("Words.bool")
        return False


class Blob:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: bytes) -> bool:
        return x == b"ab"


class Maybe:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: int32 | None) -> bool:
        return x is not None


class Keeper:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: Own[str]) -> bool:
        return len(x) > 2


class Narrow:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: int32) -> bool:
        return x == 5


@nocopy
class Pt:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Marker:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: Pt) -> bool:
        return x.n == 1


class MaybePt:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: Pt | None) -> bool:
        return x is not None and x.n == 1


ANCHOR = Pt(1)


class Identity:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: Pt) -> bool:
        return x is ANCHOR


class Lists:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: list[int16]) -> bool:
        return x[0] == 1


class Firsts:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __contains__(self, x: list[int32] | None) -> bool:
        if x is None:
            return False
        print(self.tag + ": contains", x[0])
        return x[0] < 2


def mark(tag: str, x: int32) -> int32:
    print(tag + ": mark", x)
    return x


class Pairs:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: tuple[int32, int32]) -> bool:
        return x[0] < x[1]


class OneT[T]:
    def __init__(self) -> None:
        pass

    def __contains__(self, x: T) -> bool:
        return True


class Holder:
    b: B

    def __init__(self) -> None:
        self.b = B()

    def probe(self, n8: int8) -> bool:
        # A method body with a field receiver: the same conversion.
        return n8 in self.b  # tpyc: ok


def value() -> None:
    b = B()
    n8: int8 = 1
    # The int8 needle converts at the int16 winner, as the explicit call's.
    r = n8 in b  # tpyc: ok
    c = b.__contains__(n8)
    print("value:", r, c)


def negated() -> None:
    b = B()
    n8: int8 = 1
    # `not in` runs the same winner.
    r = n8 not in b  # tpyc: ok
    print("negated:", r)


def literal() -> None:
    b = BLit()
    # A literal needle ranks as the call's literal argument: int32, the
    # default int width.
    r = 3 in b  # tpyc: ok
    n = 3 not in b  # tpyc: ok
    c = b.__contains__(3)
    print("literal:", r, n, c)


def generic_class() -> None:
    g = G[int32]()
    n8: int8 = 1
    # A generic class receiver: the same conversion.
    r = n8 in g  # tpyc: ok
    print("generic_class:", r)


def via[T](g: G[T], n8: int8) -> bool:
    # A generic function's receiver: the same conversion.
    return n8 in g  # tpyc: ok


def generic_function() -> None:
    n8: int8 = 1
    r = via(G[int32](), n8)
    print("generic_function:", r)


def strings() -> None:
    w = Words()
    s = Blob()
    # A str literal needle takes the str winner, not the bool variant; a
    # bytes literal needle reads through the bytes parameter's view.
    r = "x" in w  # tpyc: ok
    ab = b"ab" in s  # tpyc: ok
    cd = b"cd" not in s  # tpyc: ok
    print("strings:", r, ab, cd)


def method_field() -> None:
    n8: int8 = 1
    r = Holder().probe(n8)
    print("method_field:", r)


def comprehension() -> None:
    b = B()
    xs: list[int8] = [1, 2]
    # A comprehension filter: the needle converts per element.
    ys = [x for x in xs if x in b]  # tpyc: ok
    print("comprehension:", len(ys))


def optional_slot() -> None:
    m = Maybe()
    n: int32 = 1
    # A plain int32 needle at an `int32 | None` parameter: the explicit
    # call's Optional wrap.
    r = n in m  # tpyc: ok
    nr = n not in m  # tpyc: ok
    o: int32 | None = None
    # An Optional needle passes as it is.
    ro = o in m  # tpyc: ok
    print("optional_slot:", r, nr, ro)


def owned_str() -> None:
    k = Keeper()
    s = "ab" + str(len("xyz"))
    # An `Own[str]` parameter takes a copy of the needle the caller keeps.
    r = s in k  # tpyc: ok
    print("owned_str:", r, s)


def bigint_needle() -> None:
    k = Narrow()
    big: int = 5
    # A runtime `int` needle narrows (checked) at the int32 parameter. At
    # the int16/int32 set it is ambiguous, for the call as for `in`.
    r = big in k  # tpyc: ok
    print("bigint_needle:", r)


def record_needle() -> None:
    m = Marker()
    p = Pt(1)
    # A `@nocopy` record needle is lent, not copied.
    r = p in m  # tpyc: ok
    # A temporary receiver, as the explicit call takes it.
    t = p in Marker()  # tpyc: ok
    print("record_needle:", r, t)


def optional_check(o: Pt | None) -> bool:
    h = MaybePt()
    # An Optional `@nocopy` record needle at a `Pt | None` parameter.
    return o in h  # tpyc: ok


def optional_record() -> None:
    p = Pt(1)
    print("optional_record:", optional_check(p), optional_check(None))


def identity_check(p: Pt) -> None:
    b = Identity()
    # The needle is the caller's object: `is` sees it, as in the call.
    r = p in b  # tpyc: ok
    nr = p not in b  # tpyc: ok
    c = b.__contains__(p)
    print("identity:", r, nr, c)


def identity() -> None:
    identity_check(ANCHOR)
    identity_check(Pt(1))


def pending_list() -> None:
    b = Lists()
    # A list-literal needle settles at the `list[int16]` parameter.
    r = [1] in b  # tpyc: ok
    n = [1] not in b  # tpyc: ok
    c = b.__contains__([1])
    print("pending_list:", r, n, c)


def temp_comprehension() -> None:
    f = Firsts("comp")
    # A temporary-bearing needle in a comprehension filter: once per element.
    xs = [i for i in range(3) if [mark("comp", i)] in f]  # tpyc: ok
    print("comp:", xs)


def temp_logic() -> None:
    f = Firsts("logic")
    yes = True
    no = False
    # A temporary-bearing needle on a short-circuited operand runs only when
    # that operand does.
    a = no and [mark("logic", 10)] in f  # tpyc: ok
    b = yes or [mark("logic", 11)] in f  # tpyc: ok
    c = yes and [mark("logic", 1)] in f  # tpyc: ok
    d = no or [mark("logic", 2)] in f  # tpyc: ok
    print("logic:", a, b, c, d)


def temp_while() -> None:
    f = Firsts("while")
    i: int32 = 0
    # A while condition: the needle is built again per test.
    while [mark("while", i)] in f:  # tpyc: ok
        i += 1
    print("while:", i)


def temp_gen() -> Iterator[int32]:
    f = Firsts("gen")
    for i in range(3):
        # A generator body: the needle's temporary across no suspension.
        if [mark("gen", i)] in f:  # tpyc: ok
            yield i


async def temp_async() -> None:
    f = Firsts("async")
    for i in range(2):
        await asyncio.sleep(0)
        # An async body after a suspension.
        r = [mark("async", i)] in f  # tpyc: ok
        print("async:", r)


def tuple_needle() -> None:
    ps = Pairs()
    t = (1, 2)
    r = t in ps  # tpyc: ok
    print("tuple_needle:", r)


def via_t[T](g: OneT[T], x: T) -> bool:
    # A needle typed by the generic function's own T.
    return x in g  # tpyc: ok


def generic_needle() -> None:
    print("generic_needle:", via_t(OneT[int32](), 1))


def main() -> None:
    temp_comprehension()
    temp_logic()
    temp_while()
    g = list(temp_gen())
    print("gen:", g)
    asyncio.run(temp_async())
    pending_list()
    identity()
    optional_record()
    method_field()
    comprehension()
    optional_slot()
    owned_str()
    bigint_needle()
    record_needle()
    tuple_needle()
    generic_needle()
    value()
    negated()
    literal()
    generic_class()
    generic_function()
    strings()


main()

# Module level: the same conversion at a global receiver.
mb = B()
m8: int8 = 1
mr = m8 in mb  # tpyc: ok
print("module:", mr)
