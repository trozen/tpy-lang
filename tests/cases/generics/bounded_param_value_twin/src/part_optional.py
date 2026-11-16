# `T | None` of a ValueType-bounded `T`: None tests (free, method), match,
# varargs, a `T` into a `T | None` return with literal and local arguments,
# print (at a tuple `T` too), a generator over the parameter, and the bare
# `-> T` twin of a view argument whose backing is reassigned.
from typing import Iterator
from tpy import ValueType, int32


# free function: `is None` on the parameter
def pick[T: ValueType](x: T | None, d: T) -> T:
    if x is None:  # tpyc: ok
        return d
    return x


# class-bounded method: `is None` on the parameter
class P[T: ValueType]:
    def __init__(self) -> None:
        pass

    def m(self, x: T | None, d: T) -> T:
        if x is None:  # tpyc: ok
            return d
        return x

    def opt(self, x: T) -> T | None:
        return x


# `print` of the parameter, at a tuple instantiation too
class Show[T: ValueType]:
    def __init__(self) -> None:
        pass

    def show(self, x: T | None) -> None:
        print("show", x)  # tpyc: ok


# a bounded `T | None` parameter in a generator
def some[T: ValueType](x: T | None) -> Iterator[T]:
    if x is not None:  # tpyc: ok
        yield x


# the bare twin of a view argument whose backing is reassigned: the local owns
def idt[T: ValueType](x: T) -> T:
    return x


def reassigned_backing() -> None:
    n = str(12345678) * 2
    r = idt(n[1:])  # tpyc: ok
    n = "zz" * 4
    print("idt", r, n)


# `match` on the parameter
def describe[T: ValueType](x: T | None, d: T) -> T:
    match x:  # tpyc: ok
        case None:
            return d
        case _:
            return x


# a vararg of a bounded `T` into a `T | None` result
def first[T: ValueType](*xs: T) -> T | None:
    for x in xs:
        return x  # tpyc: ok
    return None


# a plain `T` parameter into a `T | None` return, with a live argument
def via[T: ValueType](x: T) -> T | None:
    return x  # tpyc: ok


# a str / bytes local argument: `T` is its view, settled inside `T | None`
def locals_into_optional() -> None:
    s = "live"
    r = via(s)  # tpyc: ok
    n = str(5)
    q = via(n)  # tpyc: ok
    b = b"by" + b"te"
    o = via(b)  # tpyc: ok
    m = P[str]().opt(s)  # tpyc: ok
    print("via_local", r, q, o, m)


def main() -> None:
    print("pick", pick(None, 4), pick(5, 4), pick(None, "d"), pick("s", "d"))
    print("method", P[int32]().m(None, 1), P[int32]().m(2, 1), P[str]().m(None, "d"), P[str]().m("s", "d"))
    print("match", describe(None, 1), describe(3, 1), describe(None, "d"), describe("m", "d"))
    a = first("a", "b")
    n = first(9, 8)
    print("first", a, n)
    print("via", via(5), via("s"))
    locals_into_optional()
    Show[tuple[int32, str]]().show(None)
    Show[int32]().show(5)
    Show[str]().show("t")
    for p in some(3):
        print("some", p)
    for q in some("s"):
        print("some", q)
    none: int32 | None = None
    for z in some(none):
        print("some", z)
    print("some none done")
    reassigned_backing()
