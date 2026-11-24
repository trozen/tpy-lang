# An operand at an operator dunder's union parameter (each pair of a
# chained comparison, an in-place operator's `__add__` / `__radd__`
# fallback), an object's `__call__` argument, a subscript store's key and a
# read-modify-write setter's value land in the member sema recorded, as the
# explicit call's argument does.
from typing import overload
from tpy import Own, ValueType, int16, int32, int64, uint32, uint64


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __add__(self, other: int32 | int64) -> bool:
        return isinstance(other, int64)

    def __sub__(self, other: int64 | uint64) -> bool:
        return isinstance(other, uint64)

    def __eq__(self, other: int32 | int64) -> bool:
        return isinstance(other, int64)

    def __lt__(self, other: int32 | int64) -> bool:
        return isinstance(other, int64)

    def __iadd__(self, other: int32 | int64) -> "Box":
        print("operators-iadd", isinstance(other, int64))
        return self


class Pinned:
    def __init__(self) -> None:
        pass

    @overload
    def __add__(self, other: int16 | int64) -> bool: ...
    @overload
    def __add__(self, other: str) -> bool: ...
    def __add__(self, other: int16 | int64 | str) -> bool:
        return isinstance(other, int16)

    @overload
    def __sub__(self, other: str | int64) -> bool: ...
    @overload
    def __sub__(self, other: float) -> bool: ...
    def __sub__(self, other: str | int64 | float) -> bool:
        return isinstance(other, str)


class Call:
    def __init__(self) -> None:
        pass

    def __call__(self, x: int32 | int64) -> bool:
        return isinstance(x, int64)


class Store:
    v: int32

    def __init__(self) -> None:
        self.v = 0

    def __getitem__(self, key: int32) -> int32:
        return self.v

    def __setitem__(self, key: int32, value: int64 | str) -> None:
        print("operators-setitem", isinstance(value, str))


class Lt:
    def __init__(self) -> None:
        pass

    def __lt__(self, x: float | str) -> bool:
        return True


class Wide:
    def __init__(self) -> None:
        pass

    def __lt__(self, x: int64 | str) -> bool:
        return not isinstance(x, str)


class Gt:
    def __init__(self) -> None:
        pass

    def __gt__(self, x: int32 | str) -> bool:
        print("operators-chain-width-gt", isinstance(x, int32))
        return True


class Gt64:
    def __init__(self) -> None:
        pass

    def __gt__(self, x: int64 | str) -> bool:
        print("operators-chain-precision", x)
        return True


class Keyed:
    def __init__(self) -> None:
        pass

    def __getitem__(self, key: int32) -> int32:
        return key

    def __setitem__(self, key: int32 | int64, value: int32) -> None:
        print("operators-store-key", isinstance(key, int64))


def big() -> int64:
    return 9007199254740993


def small() -> int32:
    return int32(5)


class Acc:
    def __init__(self) -> None:
        pass

    def __add__(self, other: int32 | int64) -> Own["Acc"]:
        print("operators-aug-fallback", isinstance(other, int32))
        return Acc()


class V(ValueType):
    n: int64

    def __init__(self, n: int64) -> None:
        self.n = n


class R:
    def __init__(self) -> None:
        pass

    def __radd__(self, other: V | int64) -> V:
        print("operators-aug-radd", isinstance(other, V))
        return V(other.n + 1 if isinstance(other, V) else other)


def chains() -> None:
    # each pair converts the shared middle value itself: `float | str`
    # takes a double, the equality compares the int64 exactly
    print("operators-chain-silent",
          Lt() < big() == int64(9007199254740992))  # tpyc: ok
    # the middle lands in int64 at `Wide` and in int32 at `Gt`
    w = Wide() < small() < Gt()  # tpyc: ok
    print("operators-chain-width", w)
    # the middle lands in float at `Lt`, in int64 (no rounding) at `Gt64`
    r = Lt() < big() < Gt64()
    print("operators-chain-precision-result", r)


def aug_fallbacks() -> None:
    a = Acc()
    c = uint32(5)
    # an in-place operator falling back to `__add__` lands its operand as
    # the explicit `a.__add__(c)` does: the widened uint32 in int64
    a += c  # tpyc: warning(/falls back to '__add__'/)
    a += int32(5)  # tpyc: warning(/falls back to '__add__'/)
    v = V(1)
    # the reflected fallback: the target is `__radd__`'s operand
    v += R()  # tpyc: ok
    print("operators-aug-radd-result", v.n)


def stores() -> None:
    k = Keyed()
    # a store's key lands in the setter's member, as the explicit
    # `k.__setitem__(int64(2), 1)` does
    k[int64(2)] = 1  # tpyc: ok
    k[int32(2)] = 1  # tpyc: ok


def main() -> None:
    chains()
    aug_fallbacks()
    stores()
    b = Box(1)
    c = uint32(3)
    # binary operand: an exact int64 keeps its member
    print("operators-add", b + int64(5))  # tpyc: ok
    print("operators-add-int32", b + int32(5))  # tpyc: ok
    # binary operand: a widened uint32 lands in int64, not uint64
    print("operators-sub", b - c)  # tpyc: ok
    # binary operand: an int32 lands in int64, the twin's widening
    print("operators-sub-int32", b - small())  # tpyc: ok
    # comparison and chained comparison operands
    print("operators-eq", b == int64(5))  # tpyc: ok
    print("operators-chain", b < int64(5) < int64(9))  # tpyc: ok
    # in-place operator operand
    b += int64(5)  # tpyc: ok
    p = Pinned()
    # a literal pinned to an overload's union parameter (int64, the wider
    # member holding it, not int16), and a str one
    print("operators-pin", p + 5)  # tpyc: ok
    print("operators-pin-str", p - "a")  # tpyc: ok
    k = Call()
    # an object's `__call__` argument
    print("operators-call", k(int64(5)))  # tpyc: ok
    print("operators-call-int32", k(int32(5)))  # tpyc: ok
    s = Store()
    # read-modify-write subscript: the setter's value lands in int64
    s[0] += 5  # tpyc: ok
