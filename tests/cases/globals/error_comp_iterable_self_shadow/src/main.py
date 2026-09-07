# A comprehension whose iterable reads the SAME global its loop variable shadows.
# Rejected rather than emitted: the global lives in a pointer slot the bare
# spelling would misname.
x = [10, 20, 30]
s = [x + 1 for x in x]  # tpyc: error(/expr.list_comp/)


def main() -> None:
    print(s)


main()
