# An int literal pattern over a plain Enum subject. The enum switch tier has no
# literal label to emit, so admitting the arm crashed codegen; sema rejects it.
from enum import Enum


class Color(Enum):
    Red = 0
    Green = 1


def describe(c: Color) -> str:
    match c:
        case 1:  # tpyc: error(/int literal pattern not valid for subject type 'Color'/)
            return "green"
        case _:
            return "other"


def main() -> None:
    print(describe(Color.Red))


main()
