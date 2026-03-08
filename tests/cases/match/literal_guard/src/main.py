# match/case guards on string and float literal patterns
def greet(s: str, formal: bool) -> str:
    match s:
        case "hello" if formal:
            return "Good day"
        case "hello":
            return "Hey"
        case "bye" if formal:
            return "Farewell"
        case "bye":
            return "Later"
        case _:
            return "?"
    return ""

def bucket(x: float) -> str:
    match x:
        case 0.0 if True:
            return "zero"
        case 1.0:
            return "one"
        case _:
            return "other"
    return ""

def main() -> None:
    print(greet("hello", True))
    print(greet("hello", False))
    print(greet("bye", True))
    print(greet("bye", False))
    print(greet("ok", False))
    print(bucket(0.0))
    print(bucket(1.0))
    print(bucket(2.0))

main()
