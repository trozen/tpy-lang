# The lowercase scalar type names: construction, annotation slots, the enum
# mixin, a generic bound, float64 as an alias of float, char, and the warning
# for a binding that shadows one of the names, at every binding position the
# pre-scan records. The alias sections pin that the warning keys on the type's
# own name, not on the local spelling an import gave it.
from enum import Enum
from typing import Callable, Iterator

from tpy import AnyFixedInt, char, float32, float64, int8, int32, int64, uint8, uint16, uint32, uint64
from tpy import int16 as i16


# module-level statement: rebinding an imported scalar name, annotated (the
# global is registered before the module body is scanned) and plain
uint16: int32 = 7  # tpyc: warning(/'uint16' shadows the tpy type 'uint16' for the rest of this module/)
float64 = 1.5  # tpyc: warning(/'float64' shadows the tpy type 'float64' for the rest of this module/)


# free function: the widths not bound elsewhere in this case, at parameter and return slots
def widths(a: int8, b: int64, c: uint32, d: uint64) -> int64:
    return int64(a) + b + int64(c) + int64(d)


# enum mixin: a scalar name as the value base
class Level(int8, Enum):
    LOW = 1
    HIGH = 2


# generic bound: the protocol names stay CapWords
def same[T: AnyFixedInt](v: T) -> T:
    return v


# generator: a scalar name at the yield slot
def bytes_of(n: int32) -> Iterator[uint8]:
    for i in range(n):
        yield uint8(i)


# shadow warning: a parameter named after the type hides it for the body
def shadowed(uint8: uint8) -> uint8:  # tpyc: warning(/'uint8' shadows the tpy type 'uint8'/)
    return uint8


# shadow warning: a local first bound under the type's name
def shadowed_local(s: str) -> str:
    char = s[0]  # tpyc: warning(/'char' shadows the tpy type 'char'/)
    return char


# shadow warning: tuple-unpack target
def shadowed_unpack(p: tuple[int32, int32]) -> int32:
    int64, other = p  # tpyc: warning(/'int64' shadows the tpy type 'int64'/)
    return int64 + other


# shadow warning: walrus target
def shadowed_walrus(n: int32) -> int32:
    if (uint32 := n + 1) > 0:  # tpyc: warning(/'uint32' shadows the tpy type 'uint32'/)
        return uint32
    return 0


# shadow warning: for-loop target
def shadowed_for(xs: list[int32]) -> int32:
    total = 0
    for uint64 in xs:  # tpyc: warning(/'uint64' shadows the tpy type 'uint64'/)
        total += uint64
    return total


class Ctx:
    def __enter__(self) -> int32:
        return 3

    def __exit__(self, et, ev, tb) -> None:
        pass


# shadow warning: with-as target
def shadowed_with() -> int32:
    with Ctx() as float32:  # tpyc: warning(/'float32' shadows the tpy type 'float32'/)
        return float32


# shadow warning: a nested def named after the type (not called: a call to a
# nested def that shadows an import is a lowering reject, BUGS.md#nested-def-shadowing-builtin-call-rejected)
def shadowed_nested() -> int32:
    def int8() -> int32:  # tpyc: warning(/'int8' shadows the tpy type 'int8'/)
        return 8

    return 8


# shadow warning: comprehension variable
def shadowed_comp(xs: list[int32]) -> int32:
    return sum([int32 for int32 in xs])  # tpyc: warning(/'int32' shadows the tpy type 'int32' within its scope/)


# shadow warning: except-as binding
def shadowed_except(n: int32) -> int32:
    try:
        if n < 0:
            raise ValueError("neg")
        return n
    except ValueError as int32:  # tpyc: warning(/'int32' shadows the tpy type 'int32' within its scope/)
        return -1


# shadow warning: lambda parameter
def shadowed_lambda(n: int32) -> int32:
    f: Callable[[int32], int32] = lambda uint8: uint8 + 1  # tpyc: warning(/'uint8' shadows the tpy type 'uint8' within its scope/)
    return f(n)


# shadow warning: match-arm capture
def shadowed_match(n: int32) -> int32:
    match n:
        case 0:
            return 0
        case uint32:  # tpyc: warning(/'uint32' shadows the tpy type 'uint32' within its scope/)
            return uint32


# alias rule: the type was imported as i16, so a local int16 is no shadow
def alias_free() -> int32:
    int16 = 3  # tpyc: ok
    return int16


# alias rule: the local spelling of the import is what shadows
def alias_shadow() -> int32:
    i16 = 4  # tpyc: warning(/'i16' shadows the tpy type 'int16'/)
    return i16


def main() -> None:
    print("widths", widths(1, 2, 3, 4))
    print("ctor", int32(7), uint8(255), float32(1.5))
    print("enum", Level.HIGH, Level.LOW.value)
    print("bound", same(int32(21)), same(uint64(3)))
    print("gen", list(bytes_of(3)))
    # float64 is float: the alias resolves to the same type
    f: float = 2.5
    print("float64", f)
    print("char", char("x"))
    print("shadow-param", shadowed(9))
    print("shadow-local", shadowed_local("abc"))
    print("shadow-unpack", shadowed_unpack((1, 2)))
    print("shadow-walrus", shadowed_walrus(1))
    print("shadow-for", shadowed_for([1, 2, 3]))
    print("shadow-with", shadowed_with())
    print("shadow-nested", shadowed_nested())
    print("shadow-comp", shadowed_comp([4, 5]))
    print("shadow-except", shadowed_except(6), shadowed_except(-6))
    print("shadow-lambda", shadowed_lambda(2))
    print("shadow-match", shadowed_match(0), shadowed_match(5))
    print("alias-free", alias_free())
    print("alias-shadow", alias_shadow())
    print("module", uint16, float64)


main()
