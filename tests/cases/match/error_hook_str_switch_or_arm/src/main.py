# The adjacent shape to a suspending str-literal `match`: an OR-arm. Its body
# is emitted once per alternative string, which for a suspending arm would walk
# one frame block twice and duplicate its resume states -- so it is rejected.
from typing import Iterator


def gen(s: str) -> Iterator[str]:  # tpyc: error(/not yet supported.*res.match_strategy/)
    match s:
        case "a" | "b":
            yield "ab"
        case "c":
            yield "3"
        case "d":
            yield "4"
        case "e":
            yield "5"
        case "f":
            yield "6"
        case _:
            yield "0"


def main() -> None:
    for v in gen("a"):
        print(v)


main()
