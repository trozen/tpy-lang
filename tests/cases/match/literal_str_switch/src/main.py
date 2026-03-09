# string match with >= 5 cases triggers switch-based dispatch
def classify(s: str) -> str:
    match s:
        case "red":
            return "color"
        case "green":
            return "color"
        case "blue":
            return "color"
        case "cat":
            return "animal"
        case "dog":
            return "animal"
        case "bird":
            return "animal"
        case _:
            return "unknown"

def with_guard(cmd: str, verbose: bool) -> str:
    match cmd:
        case "help" if verbose:
            return "verbose help"
        case "help":
            return "help"
        case "quit":
            return "quit"
        case "save":
            return "save"
        case "load":
            return "load"
        case "undo":
            return "undo"
        case "redo":
            return "redo"
        case s:
            return "unknown: " + s

def with_or(s: str) -> str:
    match s:
        case "red" | "green" | "blue":
            return "color"
        case "cat" | "dog" | "bird":
            return "animal"
        case "one" | "two":
            return "number"
        case _:
            return "other"

def by_length(s: str) -> str:
    match s:
        case "a":
            return "one"
        case "bb":
            return "two"
        case "ccc":
            return "three"
        case "dddd":
            return "four"
        case "eeeee":
            return "five"
        case _:
            return "other"

def main() -> None:
    print(classify("red"))
    print(classify("green"))
    print(classify("dog"))
    print(classify("xyz"))
    print(classify(""))
    print(with_guard("help", True))
    print(with_guard("help", False))
    print(with_guard("quit", False))
    print(with_guard("xyz", False))
    print(with_or("blue"))
    print(with_or("one"))
    print(with_or("bird"))
    print(with_or("two"))
    print(with_or("xyz"))
    print(by_length("a"))
    print(by_length("bb"))
    print(by_length("eeeee"))
    print(by_length("zzz"))

main()
