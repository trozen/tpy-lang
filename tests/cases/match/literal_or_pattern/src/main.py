# match/case or-patterns on literal subjects (int, str)
from tpy import Int32

def classify_num(x: Int32) -> str:
    match x:
        case 1 | 2 | 3:
            return "small"
        case 4 | 5:
            return "medium"
        case _:
            return "large"
    return ""

def classify_str(s: str) -> str:
    match s:
        case "hello" | "hi":
            return "greeting"
        case "bye" | "goodbye":
            return "farewell"
        case _:
            return "unknown"
    return ""

def classify_as(x: Int32) -> str:
    match x:
        case 1 | 2 as n:
            return "small: " + str(n)
        case _:
            return "other"
    return ""

def main() -> None:
    print(classify_num(Int32(1)))
    print(classify_num(Int32(3)))
    print(classify_num(Int32(5)))
    print(classify_num(Int32(9)))
    print(classify_str("hello"))
    print(classify_str("hi"))
    print(classify_str("goodbye"))
    print(classify_str("wow"))
    print(classify_as(Int32(1)))
    print(classify_as(Int32(2)))
    print(classify_as(Int32(9)))

main()
