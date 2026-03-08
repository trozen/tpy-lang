# match/case guard that fails, falling through to later cases
from tpy import Int32

def classify(x: Int32) -> str:
    match x:
        case 1 if False:
            return "never"
        case 1:
            return "one (fallthrough)"
        case 2:
            return "two"
        case _:
            return "other"
    return ""

def main() -> None:
    print(classify(Int32(1)))
    print(classify(Int32(2)))
    print(classify(Int32(3)))

main()
