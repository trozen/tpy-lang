from typing import Optional

def f(x: Optional[str], flag: bool) -> str:
    match x:
        case None:
            return "none"
        case "a" if flag:
            return "a-flag"
        case "a":
            return "a"
        case _:
            return "other"

def main() -> None:
    print(f("a", True))
    print(f(None, True))

main()
