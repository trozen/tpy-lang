# str subject: an earlier literal arm matching must NOT fall into a later
# guarded capture arm (first-match wins); the capture arm runs only when no
# earlier arm matched.
def classify(s: str) -> None:
    match s:
        case "a":
            print("one")
        case x if len(x) >= 2:
            print("long", x)
        case _:
            print("short other")


def main() -> None:
    classify("a")
    classify("bb")
    classify("z")


main()
