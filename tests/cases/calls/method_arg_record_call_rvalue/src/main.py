# A record-returning call rvalue at a read-only record method slot -- the
# const ref binds the returned prvalue for the full expression, the same
# bare render the ctor rvalue beside it already took.
from tpy import Own, nocopy


# @nocopy: the const-ref binding is the subject, so a silent copy is an error.
@nocopy
class V3:
    x: float

    def __init__(self, x_: float) -> None:
        self.x = x_

    def add(self, v: "V3") -> Own["V3"]:
        return V3(self.x + v.x)

    def muls(self, s: float) -> Own["V3"]:
        return V3(self.x * s)


def mk(s: float) -> Own[V3]:
    return V3(s)


def main() -> None:
    a = V3(1.0)
    b = V3(2.0)
    # The subject: a METHOD-call rvalue arg, and the chained form the path
    # tracer's camera ray uses.
    print(a.add(b.muls(3.0)).x)  # tpyc: ok
    print(a.add(b.muls(3.0)).add(b.muls(4.0)).x)  # tpyc: ok
    # ... the FREE-call sibling, and the ctor rvalue the row already had.
    print(a.add(mk(4.0)).x)  # tpyc: ok
    print(a.add(V3(5.0)).x)  # tpyc: ok


main()
