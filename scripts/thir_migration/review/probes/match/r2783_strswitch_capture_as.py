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
        case x as y:
            return y

def main() -> None:
    print(f("z"))

main()
