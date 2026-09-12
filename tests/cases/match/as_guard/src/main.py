# match/case as-patterns combined with guards
from tpy import int32

def literal_as_guard(x: int32) -> str:
    match x:
        case 1 as y if y > 0:
            return "one positive"
        case 1:
            return "one"
        case _:
            return "other"
    return ""

def wildcard_as_guard(x: int32) -> str:
    match x:
        case _ as y if y > 10:
            return "big"
        case _:
            return "small"
    return ""

def main() -> None:
    print(literal_as_guard(int32(1)))
    print(literal_as_guard(int32(2)))
    print(wildcard_as_guard(int32(20)))
    print(wildcard_as_guard(int32(5)))

main()
