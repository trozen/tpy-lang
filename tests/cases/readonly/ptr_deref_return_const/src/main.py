# A method that returns its `Ptr` field's pointee is const, so a READONLY
# receiver can call it and write through the result: readonly does not reach
# through a pointer. Returning self's own storage stays non-const. The methods
# live in `holder.py` because a same-module readonly receiver cannot call an
# inferred-const method yet (BUGS.md#inferred-const-invisible-to-readonly-receiver).
from tpy import Ptr, readonly
from holder import A, Holder, M


def through_readonly(m: readonly[M], h: readonly[Holder[A]]) -> None:
    m.direct().n += 1  # tpyc: ok
    m.pick(False).n += 10  # tpyc: ok
    m.via_local().n += 100  # tpyc: ok
    h.get().n += 1000  # tpyc: ok


def main() -> None:
    a = A(1)
    b = A(2)
    m = M(a, b)
    pa: Ptr[A] = a
    h = Holder(pa)
    through_readonly(m, h)
    print("readonly", a.n, b.n)
    # the inverse: a mutable receiver writes self's own storage
    m.mine().n += 5
    print("own", m.own.n)


main()
