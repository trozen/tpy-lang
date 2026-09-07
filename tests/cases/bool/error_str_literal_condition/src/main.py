# A str LITERAL as a condition: the empty test would be spelled over a C string
# rather than a view, so the condition rejects rather than emitting it.
def probe() -> int:
    if "":  # tpyc: error(/cond\.str_literal/)
        return 1
    return 0


def main() -> None:
    print(probe())


main()
