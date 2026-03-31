# Literal[int] works with Int64 impl param (not just Int32)
from typing import Literal, overload
from tpy import Int64


@overload
def classify(x: Literal[1, 2]) -> str: ...

@overload
def classify(x: Int64) -> str: ...

def classify(x: Int64) -> str:
    if x == 1:
        return "one"
    elif x == 2:
        return "two"
    return "other"


def main() -> None:
    print(classify(1))
    print(classify(2))

    x: Int64 = 99
    print(classify(x))


main()
