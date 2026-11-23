# Lending is decided at the call's instantiated signature, as the concrete
# twin's: a number lends nothing (`range(1, len(xs))`, a generic generator
# over numbers, a generic `pick` at `int32`, a generic stub's result
# returned from an `int32` function), a class does -- across a
# yield, and warned at a temporary. An opaque native class may hold the
# object its lookup returns.
from typing import Iterator
from tpy import int32, Own
from tpy.extern import native, cpp_template
from fixtures import Node


@native
def gfirst[T](n: T) -> T: ...


@cpp_template("::identity({0})")
def same[T](n: T) -> T: ...


def same_num() -> int32:
    x = 9
    # at `int32` the result is a number: returning it over a local is fine
    return same(x)  # tpyc: ok


@native
class Reg:
    tag: int32


@native
def lookup(r: Reg) -> Node: ...


def cnt[T](a: T, b: T) -> Iterator[T]:
    yield a
    yield b


def cntc(a: int32, b: int32) -> Iterator[int32]:
    yield a
    yield b


def pick[T](a: T, b: T) -> T:
    return b


def mk(v: int32) -> Own[Node]:
    return Node(v)


def gen(n: Node) -> Iterator[int32]:
    # generic stub at a class: the alias of `n` survives the yield
    r = gfirst(n)  # tpyc: ok
    yield r.v
    r.v = 9
    yield n.v


def main() -> None:
    xs = [1, 2, 3, 4]
    s = 0
    # range over a temporary number: nothing is borrowed
    for i in range(1, len(xs)):  # tpyc: ok
        s += i
    # generic generator at int32, as its concrete twin below
    for i in cnt(1, len(xs)):  # tpyc: ok
        s += i
    for i in cntc(1, len(xs)):  # tpyc: ok
        s += i
    # generic function at int32
    k = pick(1, len(xs))  # tpyc: ok
    print("generic numbers:", s + k, same_num())
    print("generic frame:", [v for v in gen(Node(1))])
    # generic stub over a temporary class: the result may be the temporary
    t = gfirst(mk(5))  # tpyc: warning(/Result borrows from temporary argument 'n'/)
    print("generic temp: bound")
    # opaque native class: its lookup's result may live in it
    n = lookup(Reg(1))  # tpyc: warning(/Result borrows from temporary argument 'r'/)
    print("lookup temp: bound")
