# A borrow-returning @property read bound off a receiver built INSIDE a loop.
# What the getter lends is the receiver's storage, so a binding that outlives
# the iteration would point into a destroyed object. The read IS the field read
# spelled as a call, so it gets the field spelling's answer: the receiver is
# hoisted to one function-scope slot and the escape WARNS that both names now
# share it -- a declared CPython divergence, not a dangle.
# The subject is the warning plus what is readable afterwards: the binding must
# see the LAST iteration's object, and the receiver name must still reach the
# same storage, which is exactly what the warning says will happen.
# The storage-ref flavour of this position has no render and stays a located
# reject (`decl.opt_reseat_source`), pinned by the sweep's
# `loop_decl__opt__prop__local` cell rather than by a case.
from tpy import int32


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    m: Rec

    def __init__(self, tag: int32) -> None:
        self.m = Rec(tag)

    @property
    def rec(self) -> Rec:
        return self.m

    def rec_m(self) -> Rec:
        return self.m


def main() -> None:
    holder = Rec(0)
    for i in range(3):
        b = B(i)
        # the getter read: warned and hoisted, like `b.m` one spelling over
        holder = b.rec  # tpyc: warning(/will not keep the object it was given/)
    print("loop_local:", holder.x)
    # the alias is LIVE after the loop, and it is the same storage the
    # receiver name still reaches -- the sharing the warning announces
    holder.x += 100
    print("shared:", b.m.x)

    # the FIELD twin, one spelling over: the same warning, the same answer
    fld = Rec(0)
    for j in range(3):
        c = B(j)
        fld = c.m  # tpyc: warning(/will not keep the object it was given/)
    print("field_twin:", fld.x)


main()
