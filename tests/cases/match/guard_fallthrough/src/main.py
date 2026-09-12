# match/case guard that fails, falling through to later cases
from tpy import int32

def classify(x: int32) -> str:
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
    print(classify(int32(1)))
    print(classify(int32(2)))
    print(classify(int32(3)))

main()
