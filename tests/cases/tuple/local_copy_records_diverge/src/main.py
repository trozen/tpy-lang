# A tuple name read live, or a lending call's result, held in a local
# literal's owned storage is a warned copy: a write through the literal's
# element after the binding does not reach the source (CPython aliases it).
from tpy import int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


# a live local member: t is read after u binds it
def live_member() -> None:
    t = (A(1), 2)
    u = (t, 1)  # tpyc: warning(/copies A into owned storage \(tuple element 0.0\)/)
    u[0][0].v = 9
    print("live_member", t[0].v, u[0][0].v)


# a live local member one literal deeper
def live_nested() -> None:
    t = (A(1), 2)
    u = ((t, 1), 3)  # tpyc: warning(/copies A into owned storage \(tuple element 0.0.0\)/)
    u[0][0][0].v = 9
    print("live_nested", t[0].v, u[0][0][0].v)


# a borrowed parameter member: the caller's tuple stays the caller's
def param_member(t: tuple[A, int32]) -> None:
    u = (t, 1)  # tpyc: warning(/copies A into owned storage \(tuple element 0.0\)/)
    t[0].v = 9
    print("param_member", t[0].v, u[0][0].v)


class K:
    a: A

    def __init__(self) -> None:
        self.a = A(1)

    def get(self) -> A:
        return self.a


# a lending call member: the local's literal holds a copy of its result
def call_member() -> None:
    k = K()
    t = (k.get(), 1)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    t[0].v = 9
    print("call_member", k.get().v, t[0].v)


# a free function's local named `self` is an ordinary local
def self_local(t: tuple[A, int32]) -> None:
    self = (t, 1)  # tpyc: warning(/copies A into owned storage \(tuple element 0.0\)/)
    self[0][0].v = 7
    print("self_local", t[0].v, self[0][0].v)


def main() -> None:
    call_member()
    live_member()
    live_nested()
    param_member((A(1), 2))
    self_local((A(1), 2))


main()
