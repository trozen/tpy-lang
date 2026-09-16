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

# A bool inner reads the same "bool never switches" fact as a top-level bool
# subject: a C++ `switch` over a bool is -Wswitch-bool, so both take the chain.
def flagged(b: Optional[bool]) -> str:
    match b:
        case None:
            return "none"
        case True:  # tpyc: ok
            return "yes"
        case False:  # tpyc: ok
            return "no"
        case _:
            return "unreachable"

# An int literal widened onto the bool inner reads that same chain
def flagged_int(b: Optional[bool]) -> str:
    match b:
        case None:
            return "none"
        case 1:  # tpyc: ok
            return "one"
        case 0:  # tpyc: ok
            return "zero"
        case _:
            return "other"

def main() -> None:
    print(classify(None))
    print(classify(int32(0)))
    print(classify(int32(42)))
    print(describe(None))
    print(describe("hello"))
    print(describe("world"))
    print(flagged(None), flagged(True), flagged(False))
    print(flagged_int(None), flagged_int(True), flagged_int(False))

main()
