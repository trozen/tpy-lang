# Match on Literal string type -- exhaustive, or-patterns, wildcard suppression
from typing import Literal

def classify(mode: Literal["r", "w", "rb", "wb"]) -> None:
    match mode:
        case "r" | "w":
            print("text:" + mode)
        case "rb" | "wb":
            print("binary:" + mode)

def with_wildcard(mode: Literal["a", "b", "c"]) -> None:
    match mode:
        case "a":
            print("first")
        case _:
            print("other")

def main() -> None:
    classify("r")
    classify("w")
    classify("rb")
    classify("wb")
    with_wildcard("a")
    with_wildcard("b")

main()
