# Member kinds: a recursive alias's member, a structural protocol member,
# a readonly union's member and an Optional-payload union's member each
# convert as their twin does. (A protocol beside a concrete type, and a
# @dynamic protocol in a union, are refused by sema's union rules.)
from typing import Protocol, runtime_checkable
from tpy import Own, Ptr, int32, readonly


class Leaf:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


type Tree = Leaf | list[Tree]


# recursive alias: a local moves into the wrapper's member at its return,
# as the twin's `return lf` moves it
def leaf_tree(n: int32) -> Own[Tree]:
    lf = Leaf(n)
    return lf  # tpyc: ok


def leaf_twin(n: int32) -> Own[Leaf]:
    lf = Leaf(n)
    return lf  # tpyc: ok


def count(t: Tree) -> int32:
    if isinstance(t, Leaf):
        return t.v
    total = 0
    for c in t:
        total += count(c)
    return total


# a recursive alias whose wrapper has one forwarding constructor: a member
# value at its parameter is built into the wrapper from the value, an
# operator operand and a narrowed member alike
type V2 = int32 | str | Ptr[readonly[V2]]


class Acc:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    # it counts calls: a method reading the wrapper's alternative is
    # ill-formed C++ today, the wrapper still incomplete in the class
    # (BUGS.md#nongeneric-recursive-union-incomplete-in-method)
    def __iadd__(self, o: V2) -> "Acc":
        self.n += 1
        return self


def v2_len(x: V2) -> int32:
    if isinstance(x, str):
        return len(x)
    return 0


def v2_narrowed(u: int32 | str) -> int32:
    if isinstance(u, str):
        return -1
    return v2_len(u)  # tpyc: ok


@runtime_checkable
class HasN(Protocol):
    def get_n(self) -> int32: ...


class HasM(Protocol):
    def get_m(self) -> int32: ...


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def get_n(self) -> int32:
        return self.n


class B:
    m: int32

    def __init__(self) -> None:
        self.m = 0


# structural protocol member: the conforming object itself
def proto_union(x: HasN | HasM) -> int32:
    if isinstance(x, HasN):
        return x.get_n()
    return -1


def proto_twin(x: HasN) -> int32:
    return x.get_n()


# readonly union: its member is lent as the readonly twin lends it
def ro_union(x: readonly[A | int32]) -> int32:
    if isinstance(x, A):
        return x.n
    return x


def ro_twin(x: readonly[A]) -> int32:
    return x.n


# Optional-payload union: a result (a moved local nothing else shares), an
# argument the callee writes through and a field each take the member (the twin `-> Own[A | None]` returning a
# local is BUGS.md#own-optional-result-owned-local-internal-error)
def opt_union(flag: bool) -> Own[A | B | None]:
    if flag:
        a = A(1)
        return a  # tpyc: ok
    return None


def opt_arg(x: A | B | None) -> int32:
    if isinstance(x, A):
        x.n += 1
        return x.n
    return -1


class Slot:
    u: A | B | None

    def __init__(self) -> None:
        self.u = None

    def put(self, a: Own[A]) -> None:
        self.u = a  # tpyc: ok


type Branch[T] = T | list[Branch[T]]


def mk_u() -> int32 | str:
    return 5


def mk_opt() -> int32 | None:
    return 5


# a recursive alias whose leaf is a union of its own: a value of that
# union (a fresh one a call hands back) is a member value the wrapper is
# built from (the result has no reader yet,
# BUGS.md#union-leaf-recursive-alias-result-unread: the bodies' C++ is the
# subject)
def wrap_u() -> Own[Branch[int32 | str]]:
    return mk_u()  # tpyc: ok


def wrap_opt() -> Own[Branch[int32 | None]]:
    return mk_opt()  # tpyc: ok


def main() -> None:
    lf = Leaf(5)
    print("rec", count(leaf_tree(3)), leaf_twin(4).v, count(lf))
    a = A(3)
    print("proto", proto_union(a), proto_twin(a))
    print("ro", ro_union(a), ro_twin(a))
    r = opt_union(True)
    if isinstance(r, A):
        print("opt", r.n)
    # the argument is the caller's object: the callee's write shows here
    print("opt-arg", opt_arg(a), a.n)
    s = Slot()
    s.put(A(7))
    print("opt-field", s.u is not None)
    acc = Acc(10)
    w = "a" + "b"
    acc += "hi"  # tpyc: ok
    acc += w  # tpyc: ok
    print("wrapper", acc.n, v2_narrowed(5))
