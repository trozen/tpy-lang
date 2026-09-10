# Error: a @overload stub declares a signature of the trailing
# implementation, so it cannot itself be a @native binding (that is @dispatch).
from typing import overload
from tpy import Int32
from tpy.extern import native


@overload
@native("std::abs")
def mag(x: Int32) -> Int32: ...  # tpyc: error(/@overload 'mag' cannot also be @native or @cpp_template/)


@overload
def mag(x: float) -> float: ...


def mag(x: Int32 | float) -> Int32 | float:
    return x


def main() -> None:
    print(mag(1))


main()
