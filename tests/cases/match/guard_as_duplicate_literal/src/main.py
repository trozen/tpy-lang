# A guarded as-pattern arm whose guard (reading its own as-binding) fails
# must fall through to the later duplicate-literal arm.
def classify(s: str) -> None:
    match s:
        case "a" as v if len(v) > 5:
            print("never", v)
        case "a":
            print("plain a")
        case _:
            print("other")


def main() -> None:
    classify("a")
    classify("b")


main()
