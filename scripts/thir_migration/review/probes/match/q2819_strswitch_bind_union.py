from tpy import int32

def f(s: str, u: str | int32) -> str:
    if isinstance(u, str):
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
            case u:
                return u
    return "n"

def main() -> None:
    print(f("z", "q"))

main()
