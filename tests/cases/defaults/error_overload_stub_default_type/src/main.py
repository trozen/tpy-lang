# An @overload stub's default is type-checked like any other: the
# specialization codegen synthesizes is built from the STUB's params, so an
# ill-typed default there reaches the emitted signature even though the
# implementation's own default is well-typed.
from typing import overload

from tpy import int32


@overload
def width(x: int32, s: str = 5) -> int32:  # tpyc: error(/expected str, got IntLiteral/)
    ...


@overload
def width(x: int32) -> int32:
    ...


def width(x: int32, s: str = "a") -> int32:
    return x + len(s)


def main() -> None:
    print(width(1, "yz"))


main()
