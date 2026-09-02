def f(s: str) -> str:
    match s:
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
        case "f" | _:
            return "0"
    return "tail"

def main() -> None:
    print(f("a"))

main()
