# Error: a call INFERRING `T = Own[P]` of a generic holding `T` in a tuple or an
# Optional is refused there: BUGS.md#own-type-arg-composite-leaf-refused.
from typing import Callable
from tpy import Fn, Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def own_p(p: Own[P]) -> Own[P]:
    p.n += 10
    return p


def mk() -> Own[P]:
    return P(3)


def loc_pair[T](f: Fn[[T], T], a: T) -> tuple[T, int32]:
    y = f(a)
    return (y, 1)


def own_q(p: Own[P]) -> Own[P]:
    p.n += 20
    return p


def fns() -> Callable[[Own[P]], Own[P]]:
    return own_p


def two_fns[A, B](fa: Fn[[A], A], fb: Fn[[B], B], a: A, b: B) -> tuple[B, int32]:
    fa(a)
    return (fb(b), 1)


def build[T](make: Fn[[], T], out: list[T]) -> int32:
    o: T | None = make()
    if o is not None:
        out.append(o)  # tpyc: warning(/may copy T into owned storage/)
    return 1


def main() -> None:
    # The subject: no type argument is written; `own_p` infers it.
    u = loc_pair(own_p, P(7))  # tpyc: error(/'loc_pair' infers `T = Own\[P\]` from 'own_p', which is not supported yet: 'T' appears inside 'tuple\[T, int32\]'/)
    print(u[1])
    out: list[P] = []
    # the remedy names the callable's result written `Own[T]`
    print(build(mk, out))  # tpyc: error(/'build' infers `T = Own\[P\]` from 'mk'.*declare 'make: Fn\[\[\], Own\[T\]\]'/)
    # the refused `B` is the one `own_q` bound, not `A`
    print(two_fns(own_p, own_q, P(1), P(2))[1])  # tpyc: error(/infers `B = Own\[P\]` from 'own_q'.*'B' appears inside/)
    # an argument that is no name is named by its parameter
    print(loc_pair(fns(), P(8))[1])  # tpyc: error(/infers `T = Own\[P\]` from the argument for 'f'.*pass a function returning `P`/)


main()
