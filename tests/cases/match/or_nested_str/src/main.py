# Parenthesized or-pattern groups on a str subject with enough alternatives
# to take the string-switch dispatch strategy.


def classify(s: str) -> str:
    match s:
        # Same alternatives as the flat `case "red" | "green" | "blue":`.
        case ("red" | "green") | "blue":
            return "color"
        case "cat" | ("dog" | "bird"):
            return "animal"
        case "one" | "two" | "three":
            return "number"
        case _:
            return "unknown"


def main() -> None:
    print(classify("red"))
    print(classify("green"))
    print(classify("blue"))
    print(classify("cat"))
    print(classify("dog"))
    print(classify("bird"))
    print(classify("two"))
    print(classify("zzz"))


main()
