# An @overload IMPLEMENTATION's defaults are type-checked too. The impl never
# reaches register_function, so it builds no ParamInfo -- but codegen emits its
# defaults into every specialization, and an out-of-range one wrapped silently
# rather than failing the build.
from typing import overload

from tpy import Int8, Int32


@overload
def total(a: Int32, b: Int8) -> Int32:
    ...


@overload
def total(a: Int32) -> Int32:
    ...


def total(a: Int32, b: Int8 = 201) -> Int32:  # tpyc: error(/201 is outside Int8 range/)
    return a + Int32(b)


def main() -> None:
    print(total(1))


main()
