# A scalar member of a union that also holds a record lands as it does
# in the all-scalar union: the conversion reads the member sema recorded,
# never the union's own converting constructor. Each face sits beside its
# neighbour, an exact record member at the same position.
from tpy import Own, String, dispatch, float32, int16, int32, int64, uint64


class A:
    def __init__(self, n: int32) -> None:
        self.n = n


# return: a literal lands in the first of int32 / int64 holding it
def ret_zero() -> Own[A | int32]:
    return 0  # tpyc: ok


def ret_narrow() -> Own[A | int16 | int64]:
    return 5  # tpyc: ok


def ret_wide() -> Own[A | int | int64]:
    return 5  # tpyc: ok


def ret_int32_last() -> Own[A | int64 | int32]:
    return 5  # tpyc: ok


def ret_int16_int32() -> Own[A | int16 | int32]:
    return 5  # tpyc: ok


def ret_float_int64() -> Own[A | float | int64]:
    return 5  # tpyc: ok


def ret_int64() -> Own[A | int64]:
    return 5  # tpyc: ok


def ret_opt() -> Own[A | int64 | None]:
    return 5  # tpyc: ok


# return: a literal no fixed width holds lands in `int`, as at `uint64 | int`
def ret_big_twin() -> uint64 | int:
    return 9223372036854775808  # tpyc: ok


def ret_big() -> Own[A | uint64 | int]:
    return 9223372036854775808  # tpyc: ok


# return: a float literal lands in the float member, a str literal in `str`
# (built, not called: no caller of this union's result lowers yet)
def ret_float() -> Own[A | float32 | int]:
    return 2.5  # tpyc: ok


def ret_str() -> Own[A | str | String]:
    return "hi"  # tpyc: ok


# return: the exact record member, unchanged
def ret_record() -> Own[A | int32]:
    return A(7)  # tpyc: ok


class Bag:
    def __init__(self, x: int32) -> None:
        self.x = x

    @dispatch
    def __eq__(self, other: "Bag") -> bool:
        return self.x == other.x

    @dispatch
    def __eq__(self, other: int32) -> bool:
        return True

    @dispatch
    def __eq__(self, other: int64) -> bool:
        return False


class H:
    f: A | int32 | int64

    def __init__(self, c: int64) -> None:
        self.f = c  # tpyc: ok

    # field store: a literal, a narrower width and an exact width
    def put_lit(self) -> None:
        self.f = 7  # tpyc: ok

    def put16(self, c: int16) -> None:
        self.f = c  # tpyc: ok

    def put64(self) -> None:
        self.f = int64(7)  # tpyc: ok

    def put_record(self) -> None:
        self.f = A(1)  # tpyc: ok


class Z:
    f: A | int32

    def __init__(self) -> None:
        self.f = 0  # tpyc: ok


def main() -> None:
    r0 = ret_zero()
    if isinstance(r0, A):
        print("mixed-ret-zero", "A", r0.n)
    else:
        print("mixed-ret-zero", r0)
    n = ret_narrow()
    print("mixed-ret-narrow", isinstance(n, int16))
    w = ret_wide()
    print("mixed-ret-wide", isinstance(w, A))
    r1 = ret_int32_last()
    print("mixed-ret-int32-last", isinstance(r1, int64))
    r2 = ret_int16_int32()
    print("mixed-ret-int16-int32", isinstance(r2, int16))
    r3 = ret_float_int64()
    print("mixed-ret-float-int64", isinstance(r3, float))
    r4 = ret_int64()
    print("mixed-ret-int64", isinstance(r4, A))
    o = ret_opt()
    print("mixed-ret-opt", o is None)
    t = ret_big_twin()
    print("mixed-ret-big-twin", isinstance(t, uint64))
    b = ret_big()
    print("mixed-ret-big", isinstance(b, uint64))
    f = ret_float()
    print("mixed-ret-float", isinstance(f, int))
    rr = ret_record()
    if isinstance(rr, A):
        print("mixed-ret-record", "A", rr.n)
    else:
        print("mixed-ret-record", rr)
    # membership needle: int64(5) is compared as the int64 member
    xs: list[Bag | int32 | int64] = [Bag(1)]
    print("mixed-needle", int64(5) in xs)  # tpyc: ok
    h1 = H(int64(2))
    x1 = h1.f
    print("mixed-field-ctor", isinstance(x1, int64))
    h2 = H(int64(2))
    h2.put_lit()
    x2 = h2.f
    print("mixed-field-lit", isinstance(x2, int64))
    h3 = H(int64(2))
    h3.put16(3)
    x3 = h3.f
    print("mixed-field-16", isinstance(x3, int64))
    h4 = H(int64(2))
    h4.put64()
    x4 = h4.f
    print("mixed-field-64", isinstance(x4, int64))
    h5 = H(int64(2))
    h5.put_record()
    x5 = h5.f
    print("mixed-field-record", isinstance(x5, A))
    z = Z()
    y = z.f
    if isinstance(y, A):
        print("mixed-field-zero", "A", y.n)
    else:
        print("mixed-field-zero", y)
