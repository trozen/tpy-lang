# match/case guard on wildcard/capture pattern with fallthrough
from tpy import int32

def classify(x: int32) -> str:
    match x:
        case _ if x > 10:
            return "big"
        case _ if x > 5:
            return "medium"
        case _:
            return "small"
    return ""

def describe(x: int32) -> str:
    match x:
        case n if n == 0:
            return "zero"
        case n:
            return "nonzero: " + str(n)
    return ""

def main() -> None:
    print(classify(int32(20)))
    print(classify(int32(7)))
    print(classify(int32(3)))
    print(describe(int32(0)))
    print(describe(int32(42)))

main()
