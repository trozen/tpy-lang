# The warned copies at an owned parameter, with the source printed after the
# call so the copy's divergence from CPython's alias shows in the output.
from tpy import Own, int32
from tpy.extern import cpp_template


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    def __init__(self, a: Own[A]) -> None:
        self.a = a


class PH:
    p: A

    def __init__(self) -> None:
        self.p = A(42)

    def borrow(self) -> A:
        return self.p


def eat_a(a: Own[A]) -> int32:  # tpyc: warning(/param 'a' is never consumed/)
    a.v += 100
    return a.v


def keep(p: Own[A]) -> Own[A]:
    p.v += 1
    return p


@cpp_template("(!::tpy::opt_has_value({0}))")
def empty(x: A | None) -> bool: ...


def main() -> None:
    h = H(A(7))
    # own_field: the field's warned copy; the holder keeps its own value
    print("own_field", eat_a(h.a), h.a.v)  # tpyc: warning(/copies A into owned storage/)
    # own_borrow_call: a borrow-returning call's warned copy
    ph = PH()
    d = keep(ph.borrow())  # tpyc: warning(/copies A into owned storage/)
    print("own_borrow_call", d.v, ph.p.v)
    # walrus_own: the walrus target's warned copy
    r = eat_a(o := A(1))  # tpyc: warning(/copies A into owned storage/)
    print("walrus_own", r, o.v)
    # template_pointer_param: a field at an inline template's declared
    # `A | None` parameter is lent, as at the plain function twin
    print("template_pointer_param", empty(h.a))  # tpyc: ok


main()
