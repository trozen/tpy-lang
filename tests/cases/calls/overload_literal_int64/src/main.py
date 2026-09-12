# Literal[int] works with int64 impl param (not just int32)
from typing import Literal, overload
from tpy import int64


@overload
def classify(x: Literal[1, 2]) -> str: ...

@overload
def classify(x: int64) -> str: ...

def classify(x: int64) -> str:
    if x == 1:
        return "one"
    elif x == 2:
        return "two"
    return "other"


def main() -> None:
    print(classify(1))
    print(classify(2))

    x: int64 = 99
    print(classify(x))


main()
