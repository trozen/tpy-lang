# A @dispatch method's argument is analyzed once, positional or keyword:
# a consuming call inside it is consumed once, and an Own[B] parameter
# read beside its own consume copies with a warning, as a local does (the
# copy is the warned divergence: CPython aliases, so nothing is printed
# through the second argument).
from typing import Self
from tpy import Own, dispatch, int32


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


def ident[T](x: Own[T]) -> Own[T]:
    return x


class O:
    @dispatch
    def m(self, x: B) -> str:
        return f"B {x.v}"

    @dispatch
    def m(self, x: int32) -> str:
        return f"int32 {x}"

    @dispatch
    def m(self, x: str) -> str:
        return f"str {x}"

    @dispatch
    def m(self, x: dict[str, int32]) -> str:
        return f"dict {x['k']}"

    @dispatch
    def pair(self, a: B, x: B) -> str:
        a.v += 1000
        return f"pair {a.v}"

    @dispatch
    def pair(self, a: str, x: B) -> str:
        return "pair str"


# The consume copies, so the parameter itself is never consumed.
def owned_param(b: Own[B]) -> None:  # tpyc: warning(/Own\[B\] param 'b' is never consumed/)
    # The Own[B] parameter read beside its own consume: a copy, as a local's.
    print("own-param:", O().pair(b, b.take()))  # tpyc: warning(/copies B into owned storage/)


def main() -> None:
    o = O()
    flag = True
    # name: the parameter aliases the local, so the callee's write shows
    a = B(1)
    print("name:", o.pair(a, B(0)), a.v)
    b = B(2)
    print("keyword:", o.m(x=b.take()))  # tpyc: ok
    b = B(4)
    print("field:", o.m(b.take().v))  # tpyc: ok
    b = B(5)
    print("select-arm:", o.m(f"{b.take().v}" if flag else "none"))  # tpyc: ok
    # (a list argument is the free function case's: C++ re-picks a method
    # variant for a braced list, BUGS.md#dispatch-method-variant-reresolved-by-cpp)
    b = B(7)
    print("dict:", o.m({"k": b.take().v}))  # tpyc: ok
    b = B(8)
    print("nested-generic:", o.m(ident(b.take())))  # tpyc: ok
    owned_param(B(9))


main()
