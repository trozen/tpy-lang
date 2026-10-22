# A fixed-width overflow -- an operation leaving its type, or a value that
# does not fit a fixed-width slot -- raises a catchable OverflowError, at
# every position the narrowing reaches.
from typing import Literal
from tpy import (int8, int32, int64, uint8, uint16, uint32, uint64, Ptr,
                 take_ptr, error_return, ReturnException)
from tpy.unsafe import unsafe_load


class Failed(Exception, ReturnException):
    pass


class Holder:
    f8: int8

    def __init__(self) -> None:
        self.f8 = 127


# free function: an aug-assign whose result leaves int8, a 16-bit product
# past int32, and a negative value into an unsigned slot
def free_fn(a8: int8, u16: uint16, v16: uint16, i32: int32) -> None:
    try:
        a8 += 1  # tpyc: ok
        print("free unreachable", a8)
    except OverflowError:
        print("free aug OverflowError")
    try:
        r = u16 * v16
        print("free unreachable", r)
    except OverflowError:
        print("free mul OverflowError")
    try:
        x: uint32 = i32  # tpyc: ok
        print("free unreachable", x)
    except OverflowError:
        print("free unsigned OverflowError")


# a store into a field and into a container element
def stores(h: Holder, xs8: list[int8], v: int32) -> None:
    try:
        h.f8 += 1  # tpyc: ok
    except OverflowError:
        print("stores field OverflowError", h.f8)
    try:
        xs8.append(v)  # tpyc: ok
    except OverflowError:
        print("stores element OverflowError", len(xs8))


# lookups: an out-of-range key raises where CPython finds nothing
def lookups(v: int32) -> None:
    xs8: list[int8] = [44]
    d8: dict[int8, int32] = {44: 1}
    try:
        print(xs8.index(v))  # tpyc: warning(/lookup key narrows from int32 to int8/)
    except OverflowError:
        print("lookups index OverflowError")
    try:
        print(d8[v])  # tpyc: warning(/lookup key narrows/)
    except OverflowError:
        print("lookups subscript OverflowError")


# @error_return body: the overflow is an exception, not the error channel
@error_return(Failed)
def er_body(a8: int8) -> int8:
    if a8 < 0:
        raise Failed()
    return a8 * 2  # tpyc: ok


class S:
    def __init__(self) -> None:
        pass

    def __mul__(self, x: int8) -> int32:
        return x


def pick(f: bool) -> Literal[300, 5]:
    if f:
        return 300
    return 5


def take64(x: uint64) -> uint64:
    return x


def rebind(n: int32, big: int64) -> int32:
    n = big  # tpyc: ok
    return n


# stores that used to be refused, at values that do not fit
def former_refusals() -> None:
    a = -1
    try:
        print(take64(a))  # tpyc: ok
    except OverflowError:
        print("refusals negative into uint64 OverflowError")
    try:
        print(rebind(1, 5000000000))
    except OverflowError:
        print("refusals parameter OverflowError")
    ys = [1, 300]
    try:
        n: int8 = ys[1]  # tpyc: ok
        print(n)
    except OverflowError:
        print("refusals element OverflowError")
    try:
        s = S()
        print(s * pick(True))  # tpyc: ok
    except OverflowError:
        print("refusals literal value OverflowError")
    x = uint8(42)
    try:
        y: uint8 = -x  # tpyc: ok
        print(y)
    except OverflowError:
        print("refusals negation OverflowError")
    big: int64 = 99999999999
    try:
        print(OSError(big, "msg"))  # tpyc: ok
    except OverflowError:
        print("refusals errno OverflowError")
    try:
        z: uint8 = ~x  # tpyc: ok
        print(z)
    except OverflowError:
        print("refusals invert OverflowError")
    # an int32 offset converts into the uint32 parameter (a CPython stub
    # pointer cannot be built from a scalar, so this stays here)
    v: int32 = 5
    p: Ptr[int32] = take_ptr(v)
    print("refusals offset", unsafe_load(p, int32(0)))  # tpyc: ok


class Prop:
    _v: int8

    def __init__(self) -> None:
        self._v = 0

    @property
    def v(self) -> int8:
        return self._v

    @v.setter
    def v(self, x: int8) -> None:
        self._v = x


# a property setter's argument narrows like any call argument
def setter(big: int64) -> None:
    p = Prop()
    try:
        p.v = big  # tpyc: ok
        print(p.v)
    except OverflowError:
        print("setter OverflowError", p.v)


# pow() computes in the default int; the store into int8 raises
def pow_store() -> None:
    try:
        r8: int8 = pow(int8(2), int8(7))  # tpyc: ok
        print(r8)
    except OverflowError:
        print("pow OverflowError")


# a conversion decided once its function is: the range check reads the value's
# range where the conversion stands, not the one the function ends with
def deferred_elision() -> None:
    x = int32(-1)
    try:
        y: uint32 = x  # tpyc: cast_checked(uint32)
        print(y)
    except OverflowError:
        print("deferred OverflowError")
    x = 1
    print(x)


def er_checked(a8: int8) -> None:
    try:
        print(er_body(a8))
    except Failed:
        print("er Failed")


def main() -> None:
    free_fn(int8(127), uint16(60000), uint16(60000), -1)
    stores(Holder(), [], 300)
    lookups(300)
    try:
        er_checked(int8(100))
    except OverflowError:
        print("er OverflowError")
    former_refusals()
    setter(300)
    pow_store()
    deferred_elision()


main()
