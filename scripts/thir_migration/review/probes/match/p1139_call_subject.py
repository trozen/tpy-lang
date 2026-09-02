def f() -> str:
    return "a"

def main() -> None:
    match f():
        case "a":
            print("a")
        case _:
            print("other")

main()
