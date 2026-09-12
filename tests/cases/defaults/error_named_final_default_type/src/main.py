# A Final[T] constant used as a default is type-checked against the parameter
# once its binding resolves -- the shape check alone accepted any constant.
from typing import Final

from tpy import int32

LABEL: Final[str] = "abc"


def scaled(n: int32 = LABEL) -> int32:  # tpyc: error(/expected int32, got StrView/)
    return n * 2


def main() -> None:
    print(scaled())


main()
