def f(s: str, flag: bool) -> str:
    match s:
        case x if flag:
            return "flag:" + x
        case "a":
            return "1"
        case "b":
            return "2"
        case "c":
            return "3"
        case "d":
            return "4"
        case "e":
            return "5"
        case _:
            return "0"

def main() -> None:
    print(f("a", False))

main()
