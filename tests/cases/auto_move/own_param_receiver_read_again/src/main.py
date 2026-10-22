# A consuming call on an `Own` parameter read again (same statement, or later through an
# alias) copies the receiver, warned, as a local owner does; its last use still moves. A
# local whose alias is read later in the same statement copies the same way, on every call
# path (plain, @dispatch, generic).
from typing import Protocol, Self
from tpy import Own, dispatch


class B:
    def __init__(self, v: int) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self

    def bump(self) -> int:
        self.v += 20
        return self.v


class Bumps(Protocol):
    def bump(self) -> int: ...


# reads `x` only: never consumed (true)
def f(a: B, x: Own[B]) -> int:  # tpyc: warning(/Own\[B\] param 'x' is never consumed/)
    a.v = 100
    return x.v


# mutates `x` in place only: never consumed (true)
def f2(x: Own[B]) -> int:  # tpyc: warning(/Own\[B\] param 'x' is never consumed/)
    x.v += 1
    return x.v


# mutates `x` in place only: never consumed (true)
@dispatch
def fd(x: Own[B]) -> int:  # tpyc: warning(/Own\[B\] param 'x' is never consumed/)
    x.v += 10
    return x.v


@dispatch
def fd(x: str) -> int:
    return 0


# calls a method on `x` only: never consumed (true)
def bump_g[T: Bumps](x: Own[T]) -> int:  # tpyc: warning(/Own\[T\] param 'x' is never consumed/)
    return x.bump()


class O:
    k: int

    def __init__(self) -> None:
        self.k = 0

    # reads `x` only: never consumed (true)
    @dispatch
    def g(self, a: B, x: Own[B], k: int) -> int:  # tpyc: warning(/Own\[B\] param 'x' is never consumed/)
        a.v = 100
        return x.v

    @dispatch
    def g(self, a: B, x: B, k: str) -> int:
        a.v = 200
        return x.v


# free function: the parameter is read again in the same call; its consuming
# call takes a copy, so the parameter itself is never consumed (true)
def same_call(b: Own[B]) -> None:  # tpyc: warning(/Own\[B\] param 'b' is never consumed/)
    n = f(b, b.take())  # tpyc: warning(/copies B into owned storage; use copy\(\) to make this explicit$/)
    print("same_call", n)


# method: an @dispatch callee reads the parameter again in the same call; the
# consuming call takes a copy (never consumed: true)
def dispatch_method(b: Own[B]) -> None:  # tpyc: warning(/Own\[B\] param 'b' is never consumed/)
    o = O()
    n = o.g(b, b.take(), 1)  # tpyc: warning(/copies B into owned storage; use copy\(\) to make this explicit$/)
    print("dispatch_method", n)


# free function: an alias of the parameter is read in a following statement;
# the consuming call takes a copy (never consumed: true)
def later_alias(b: Own[B]) -> None:  # tpyc: warning(/Own\[B\] param 'b' is never consumed/)
    r = b
    c = b.take()  # tpyc: warning(/copies B into owned storage; use copy\(\) to make this explicit$/)
    c.v = 7
    print("later_alias", r.v, c.v)


# free function: the receiver is the parameter's last use, so it moves (the "never
# consumed" warning is false: BUGS.md#own-param-consuming-method-call-uncredited)
def last_use(b: Own[B]) -> None:  # tpyc: warning(/Own\[B\] param 'b' is never consumed/)
    n = f2(b.take())  # tpyc: ok
    print("last_use", n)


# a local's alias read later in the same statement: the consume copies, on every path
def alias_same_statement() -> None:
    b1 = B(1)
    r1 = b1
    print("alias_plain", f2(b1.take()), r1.v)  # tpyc: warning(/copies B into owned storage; use copy\(\) to make this explicit$/)
    b2 = B(1)
    r2 = b2
    print("alias_dispatch", fd(b2.take()), r2.v)  # tpyc: warning(/copies B into owned storage; use copy\(\) to make this explicit$/)
    b3 = B(1)
    r3 = b3
    # the generic callee mutates its copy, which the alias does not see
    print("alias_generic", bump_g(b3.take()), r3.v)  # tpyc: warning(/copies B into owned storage; use copy\(\) to make this explicit$/)


def main() -> None:
    same_call(B(4))
    dispatch_method(B(4))
    later_alias(B(4))
    last_use(B(4))
    alias_same_statement()


main()
