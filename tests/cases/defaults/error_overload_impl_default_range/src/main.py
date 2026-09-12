# An @overload IMPLEMENTATION's defaults are type-checked too. The impl never
# reaches register_function, so it builds no ParamInfo -- but codegen emits its
# defaults into every specialization, and an out-of-range one wrapped silently
# rather than failing the build.
from typing import overload

from tpy import int8, int32


@overload
def total(a: int32, b: int8) -> int32:
    ...


@overload
def total(a: int32) -> int32:
    ...


def total(a: int32, b: int8 = 201) -> int32:  # tpyc: error(/201 is outside int8 range/)
    return a + int32(b)


def main() -> None:
    print(total(1))


main()
