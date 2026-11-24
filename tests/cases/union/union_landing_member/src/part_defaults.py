# Defaults at a union slot land in sema's member: a fixed-width constructor
# keeps its width (zero-argument ones included), a bytes literal is the
# owned member, an enum member and a class constant their own member -- at
# a parameter, a field, an `@auto_readonly` method and an overload.
from enum import Enum
from typing import Final, overload
from tpy import auto_readonly, int16, int32, int64, uint8, uint32, uint64


class Color(Enum):
    RED = 1


class Cfg:
    # spelled, so CPython's `isinstance(x, int16)` sees an int16 too
    LIMIT: Final[int16] = int16(5)
    WIDE: Final[uint32] = 6


def exact_default(value: int32 | int64 = int64(5)) -> None:  # tpyc: ok
    print("default-exact", isinstance(value, int64))


def zero_default(value: int32 | int64 = int64()) -> None:  # tpyc: ok
    print("default-zero", isinstance(value, int64))


def narrow_default(value: int16 | int64 = uint8(5)) -> None:  # tpyc: ok
    print("default-narrow", isinstance(value, int64))


def bytes_default(x: bytes | int32 = b"hi") -> None:  # tpyc: ok
    print("default-bytes", isinstance(x, bytes))


def empty_bytes_default(x: bool | bytes = b"") -> None:  # tpyc: ok
    print("default-empty-bytes", isinstance(x, bytes))


def enum_default(x: Color | int64 = Color.RED) -> None:  # tpyc: ok
    print("default-enum", x)


def constant_default(x: int16 | int64 = Cfg.LIMIT) -> None:  # tpyc: ok
    print("default-constant", isinstance(x, int16))


class Holder:
    a: int16 | uint8 = 5
    b: int64 | uint64 = Cfg.WIDE
    c: int32 | int64 = int64(5)
    d: int16 | int64 = uint8(5)

    def __init__(self) -> None:
        pass


class K:
    def __init__(self) -> None:
        pass

    @auto_readonly
    def get(self, v: int32 | int64 = int64(5)) -> bool:  # tpyc: ok
        return isinstance(v, int64)


@overload
def pick(x: str) -> bool: ...
@overload
def pick(x: int32, v: int32 | int64 = int64(5)) -> bool: ...
def pick(x: str | int32, v: int32 | int64 = int64(5)) -> bool:
    return isinstance(v, int64)


def main() -> None:
    exact_default()
    zero_default()
    narrow_default()
    bytes_default()
    empty_bytes_default()
    enum_default()
    constant_default()
    h = Holder()
    # field defaults land a: int16, b: int64, c: int64 (its own width),
    # d: int16 (the first width uint8 widens to) -- pinned by the generated
    # member initializers: reading a value-union field's member, even into
    # a local, rejects (BUGS.md#union-field-read-rejects)
    print("default-field", h.a, h.b, h.c, h.d)
    print("default-auto-readonly", K().get())
    print("default-overload", pick(int32(1)))
