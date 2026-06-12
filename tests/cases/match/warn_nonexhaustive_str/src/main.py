# str subject with only literal arms: coverage cannot be proven, so the
# match warns and falls through on the no-match path.
def f(s: str) -> str:
    match s:  # tpyc: warning(/non-exhaustive match.*no unconditional catch-all/)
        case "a":
            return "ay"
        case "b":
            return "bee"
    return "other"


def main() -> None:
    print(f("a"))
    print(f("b"))
    print(f("z"))


main()
