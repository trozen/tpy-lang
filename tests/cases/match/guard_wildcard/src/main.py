# match/case guard on wildcard/capture pattern with fallthrough
from tpy import Int32

def classify(x: Int32) -> str:
    match x:
        case _ if x > 10:
            return "big"
        case _ if x > 5:
            return "medium"
        case _:
            return "small"
    return ""

def describe(x: Int32) -> str:
    match x:
        case n if n == 0:
            return "zero"
        case n:
            return "nonzero: " + str(n)
    return ""

def main() -> None:
    print(classify(Int32(20)))
    print(classify(Int32(7)))
    print(classify(Int32(3)))
    print(describe(Int32(0)))
    print(describe(Int32(42)))

main()
