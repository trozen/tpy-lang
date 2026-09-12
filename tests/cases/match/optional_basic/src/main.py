# match/case on Optional subjects (T | None)
from typing import Optional
from tpy import int32

def classify(x: Optional[int32]) -> str:
    match x:
        case None:
            return "nothing"
        case 0:
            return "zero"
        case _:
            return "something"
    return ""

def describe(s: Optional[str]) -> str:
    match s:
        case None:
            return "none"
        case "hello":
            return "greeting"
        case _:
            return "other: " + s
    return ""

def main() -> None:
    print(classify(None))
    print(classify(int32(0)))
    print(classify(int32(42)))
    print(describe(None))
    print(describe("hello"))
    print(describe("world"))

main()
