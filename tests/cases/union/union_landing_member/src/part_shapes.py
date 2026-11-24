# Element paths and other value shapes: a tuple's elements each land in
# their own member, an Optional's payload union, a decided ternary's arms,
# an overload pair whose rejected union variant leaves no landing, a
# recursive alias's wrapper (a subscript store included), `*args` pack
# elements, narrow members, a member beside two that share one C++
# alternative, a pending `str` local and a pointer tuple's value element.
from typing import Literal, overload
from tpy import (Own, String, float32, int8, int16, int32, int64, uint8,
                 uint32, uint64)

type W = uint8 | int16 | list[W]
type N = int8 | str | list[N]
type U = int32 | int64 | list[U]


# tuple: each element of a tuple literal lands at its own path (passing or
# reading a tuple of value unions is BUGS.md#value-union-tuple-element-reads-reject)
def tuple_elements(c: uint32) -> None:
    t: tuple[int64 | str, int32 | str] = (c, "b")  # tpyc: ok
    print("shapes-tuple", t)


# Optional payload: a uint32 lands in the payload union's int64
def optional_payload(c: uint32) -> None:
    o: int64 | uint64 | None = c  # tpyc: ok
    if o is not None:
        print("shapes-optional", o, isinstance(o, uint64))


# ternary: each arm is checked into the union at its own operand
def ternary(c: uint32, flag: bool) -> None:
    v: int64 | uint64 = c if flag else uint64(7)  # tpyc: ok
    print("shapes-ternary", v)


@overload
def pick(x: int64) -> str: ...
@overload
def pick(x: int32 | str) -> str: ...
def pick(x: int64 | int32 | str) -> str:
    return "picked"


# overload pair: the exact variant wins; the union one was only tried
def overload_pair(n: int64) -> None:
    print("shapes-overload", pick(n))  # tpyc: ok


def tw(v: W) -> bool:
    return isinstance(v, uint8)


def make_w() -> Own[W]:
    return 5  # tpyc: ok


# recursive alias: a literal lands in its member at an argument, a
# declaration and a return, as in a plain union
def wrapper() -> None:
    print("shapes-wrapper-arg", tw(5))  # tpyc: ok
    b: W = 5  # tpyc: ok
    print("shapes-wrapper-decl", tw(b))
    print("shapes-wrapper-ret", tw(make_w()))


def tu(tag: str, v: U) -> None:
    print(tag, isinstance(v, int64))


# recursive alias subscript store: the stored value lands in its member as
# the argument twin `tu(int64(5))` does, a literal in the narrow member
def wrapper_store() -> None:
    tu("shapes-wrapper-store-arg", int64(5))  # tpyc: ok
    ds: dict[str, U] = {}
    ds["a"] = int64(5)  # tpyc: ok
    tu("shapes-wrapper-store", ds["a"])
    d: dict[str, W] = {"a": 5}
    d["b"] = 5  # tpyc: ok
    print("shapes-wrapper-store-narrow", tw(d["b"]))
    xs: list[W] = [5]
    xs[0] = 5  # tpyc: ok
    print("shapes-wrapper-store-list", tw(xs[0]))


def mk_str() -> str:
    return "abc"


def str_or_int(u: str | int32) -> bool:
    return isinstance(u, str)


# pending `str` local: a local whose view-or-owned form is settled later
# lands in `str` at a declaration and an argument
def pending_str() -> None:
    s = mk_str()
    u: str | int32 = s  # tpyc: ok
    print("shapes-pending-str-decl", isinstance(u, str))
    t = mk_str()
    print("shapes-pending-str-arg", str_or_int(t))  # tpyc: ok


class Ref:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __repr__(self) -> str:
        return f"Ref({self.n})"


# pointer tuple: its value element lands in int64 beside the lent record,
# at the declaration and at the reassignment (`static_cast<int64_t>(c)`;
# reading the element back is BUGS.md#value-union-tuple-element-reads-reject)
def pointer_tuple(c: uint32, r: Ref, q: Ref) -> None:
    t: tuple[int64 | uint64, Ref] = (c, r)  # tpyc: ok
    print("shapes-ptr-tuple-decl", t)
    t = (c, q)  # tpyc: ok
    print("shapes-ptr-tuple-rebind", t)


def tn(v: N) -> bool:
    return isinstance(v, str)


def narrow_take(x: int8 | str) -> bool:
    return isinstance(x, str)


def narrow_make() -> int8 | str:
    return 3  # tpyc: ok


class NarrowField:
    x: int8 | str

    def __init__(self) -> None:
        self.x = 3  # tpyc: ok


# narrow members: a checked `int` argument, a returned literal, a field
# init, a recursive alias's narrow member (reading a union field is
# BUGS.md#union-field-read-rejects)
def narrow(n: int) -> None:
    print("shapes-narrow-arg", narrow_take(n))  # tpyc: ok
    r = narrow_make()
    print("shapes-narrow-ret", isinstance(r, str))
    NarrowField()
    print("shapes-narrow-alias", tn(3))  # tpyc: ok


def pack(*xs: int16 | int64) -> None:
    for x in xs:
        print("shapes-pack", isinstance(x, int64))


# `*args`: each pack element lands in its own member
def star() -> None:
    pack(int8(1), int64(2))  # tpyc: ok


# a float32 value already the member converts to nothing
def float_exact() -> None:
    w: float32 | float = float32(1.5)  # tpyc: ok
    print("shapes-float32", isinstance(w, float32))


def shares(v: str | String | int32 | Literal[1]) -> bool:
    return isinstance(v, str)


# two members sharing one C++ alternative (`int32`, `Literal[1]`) leave a
# value of another alternative its own member (a declaration narrowed at a
# union with a shared alternative is error_union_landing_exempt_decl_narrow)
def shared_alternative() -> None:
    print("shapes-shared-arg", shares("a"))  # tpyc: ok


def main() -> None:
    wrapper_store()
    pending_str()
    pointer_tuple(1, Ref(2), Ref(3))
    shared_alternative()
    tuple_elements(1)
    optional_payload(2)
    ternary(3, True)
    overload_pair(4)
    wrapper()
    narrow(7)
    star()
    float_exact()
