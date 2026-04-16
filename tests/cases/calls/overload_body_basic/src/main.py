# @overload variants with bodies directly (no trailing impl)
from typing import overload


@overload
def describe(x: int) -> str:  # tpyc: ok
    return "int: " + str(x)

@overload
def describe(x: str) -> str:  # tpyc: ok
    return "str: " + x


def main() -> None:
    print(describe(42))
    print(describe("hello"))


main()
