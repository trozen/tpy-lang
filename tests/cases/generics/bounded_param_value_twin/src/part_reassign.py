# Reassigned ValueType-bounded parameters: the concrete twin's mutable copy.
from typing import Iterator
from tpy import ValueType, int32


# class-bounded method: a mutable copy, as the concrete twin's
class C[T: ValueType]:
    def __init__(self) -> None:
        pass

    def swap(self, x: T, y: T) -> T:
        x = y  # tpyc: ok
        return x


# free function
def reassign[T: ValueType](x: T, y: T) -> T:
    x = y  # tpyc: ok
    return x


# through `nonlocal` from a closure
def via_nonlocal[T: ValueType](x: T, y: T) -> T:
    def inner() -> None:
        nonlocal x
        x = y  # tpyc: ok
    inner()
    return x


# inside a generator body: the frame holds its own copy
def gen_reassign[T: ValueType](x: T, y: T) -> Iterator[T]:
    yield x
    x = y  # tpyc: ok
    yield x


# a bounded `T | None` parameter takes the fallback in place
def or_default[T: ValueType](x: T | None, d: T) -> T:
    if x is None:
        x = d  # tpyc: ok
    return x


# one bounded `T | None` parameter into another, whole
def take[T: ValueType](x: T | None, y: T | None) -> T | None:
    if y is not None:
        x = y  # tpyc: ok
    return x


# its concrete twin: the view converts into the owned optional
def or_default_bytes(x: bytes | None, d: bytes) -> bytes:
    if x is None:
        x = d  # tpyc: ok
    return x


# `None` into a bounded `T | None` parameter
def clear[T: ValueType](x: T | None, flag: bool) -> T | None:
    if flag:
        x = None  # tpyc: ok
    return x


class Clr[T: ValueType]:
    def __init__(self) -> None:
        pass

    def clear(self, x: T | None) -> T | None:
        x = None  # tpyc: ok
        return x


# a whole concrete `bytes | None` into another
def take_other(x: bytes | None, y: bytes | None) -> bytes | None:
    y = b"new"
    if y is not None:
        x = y  # tpyc: ok
    return x


def main() -> None:
    print("swap", C[int32]().swap(1, 3), C[str]().swap("a", "b"), C[bytes]().swap(b"a", b"c"))
    print("reassign", reassign(1, 3), reassign("a", "b"), reassign(b"x", b"y"))
    print("nonlocal", via_nonlocal(1, 4), via_nonlocal("n", "m"), via_nonlocal(b"p", b"q"))
    for g in gen_reassign("g1", "g2"):
        print("gen_reassign", g)
    for h in gen_reassign(5, 6):
        print("gen_reassign", h)
    print("or_default", or_default(None, 2), or_default(7, 2), or_default(None, "d"), or_default("s", "d"))
    print("or_default", or_default(None, b"r"), or_default(b"q", b"r"))
    print("or_default_bytes", or_default_bytes(None, b"d"), or_default_bytes(b"x", b"d"))
    print("reassign_tuple", reassign((1, "a"), (2, "b")))
    print("clear", clear(5, True), clear(5, False), clear("c", True), Clr[int32]().clear(3), Clr[str]().clear("m"))
    print("take_other", take_other(None, b"old"))
    print("take", take(1, 2), take(None, 3), take(4, None), take("a", "b"), take(None, "c"), take(b"a", b"b"))
