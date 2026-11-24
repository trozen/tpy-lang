# A value-type object named by a mutable lvalue is copied into its union
# member as its twin copies it -- not handed to the class's forwarding
# protocol constructor, which the alternative's direct-initialization from
# a mutable lvalue would prefer.
from typing import Protocol
from tpy import Own, int32, ValueType


class HasN(Protocol):
    def get_n(self) -> int32: ...


class VBase(ValueType):
    n: int32

    def __init__(self, src: HasN) -> None:
        self.n = src.get_n() + 1000

    def get_n(self) -> int32:
        return self.n


class VSub(VBase, ValueType):
    def __init__(self, n: int32) -> None:
        super().__init__(Plain(n))


class Plain:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n - 1000

    def get_n(self) -> int32:
        return self.n


class H:
    u: VBase | int32
    t: VBase

    def __init__(self) -> None:
        self.u = 0
        # a `VBase` field keeps only the base part of the subclass value
        self.t = VSub(0)  # tpyc: warning(/upcast narrows/)

    def held(self) -> int32:
        match self.u:
            case VBase() as b:
                return b.n
            case _:
                return -1

    # result: a field's object copied into the member (`take` writes
    # `self`, so `self.t` is a mutable lvalue the copy is spelled from)
    def take(self) -> VBase | int32:
        self.u = 1
        return self.t  # tpyc: ok


g_v: VBase = VBase(Plain(5))


# result: a value object the `return` does not move -- a global, a match
# capture, an isinstance-narrowed member, a walrus -- is copied into the
# member as the twin copies it, never handed to the protocol constructor
def ret_global() -> VBase | int32:
    return g_v  # tpyc: ok


def ret_capture(k: int32) -> VBase | int32:
    u: VBase | int32 = 0
    if k > 0:
        u = VBase(Plain(k))
    match u:
        case VBase() as b:
            return b  # tpyc: ok
        case _:
            return 0


def ret_narrowed(k: int32) -> VBase | int32:
    u: VBase | str = "none"
    if k > 0:
        u = VBase(Plain(k))
    if isinstance(u, VBase):
        return u  # tpyc: ok
    return 0


# result: an `Own` parameter's narrowed member is its const alias, copied
# as the const lvalue -- no `std::move` of it reruns the protocol
# constructor
def ret_own_narrowed(u: Own[VBase | str]) -> VBase | int32:
    if isinstance(u, VBase):
        return u  # tpyc: ok
    return 0


def ret_walrus() -> VBase | int32:
    w: VBase = VBase(Plain(1))
    return (w := VBase(Plain(5)))  # tpyc: ok


def ret_walrus_twin() -> VBase:
    w: VBase = VBase(Plain(1))
    return (w := VBase(Plain(5)))  # tpyc: ok


# result: an owned local the `return` moves moves into the member, as the
# twin's `return v;` moves it
def ret_owned(k: int32) -> VBase | int32:
    v = VBase(Plain(k))
    return v  # tpyc: ok


def ret_owned_twin(k: int32) -> VBase:
    v = VBase(Plain(k))
    return v  # tpyc: ok


def ret_twin(k: int32) -> VBase:
    u: VBase | str = "none"
    if k > 0:
        u = VBase(Plain(k))
    if isinstance(u, VBase):
        return u
    return VBase(Plain(0))


def n_of(u: VBase | int32) -> int32:
    if isinstance(u, VBase):
        return u.n
    return -1


def arg_union(u: VBase | int32) -> int32:
    if isinstance(u, VBase):
        return u.n
    return -1


def arg_twin(t: VBase) -> int32:
    return t.n


def main() -> None:
    # a `VBase` local keeps only the base part of the subclass value
    v: VBase = VSub(5)  # tpyc: warning(/upcast narrows/)
    # call argument, beside its twin
    print("value-arg", arg_union(v), arg_twin(v))  # tpyc: ok
    h = H()
    # field write
    h.u = v  # tpyc: ok
    print("value-field", h.held())
    h.t = v
    k = h.take()
    if isinstance(k, VBase):
        print("value-result", k.n)
    print("value-ret", n_of(ret_global()), n_of(ret_capture(5)),
          n_of(ret_narrowed(5)), n_of(ret_walrus()), ret_twin(5).n,
          ret_walrus_twin().n, n_of(ret_own_narrowed(VBase(Plain(5)))))
    print("value-owned", n_of(ret_owned(6)), ret_owned_twin(6).n)
