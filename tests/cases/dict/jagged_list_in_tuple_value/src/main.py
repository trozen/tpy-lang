# A jagged pending list wrapped in a tuple that is a dict VALUE: the demotion
# must reach the inner list through the tuple, across sibling dict values, so the
# value type converges (list[int]). Mutating it through the dict + tuple must be
# observed.
def main() -> None:
    d = {1: (0, [1, 2]), 2: (0, [3, 4, 5])}  # tpyc: ok
    print(d)
    d[2][1].append(9)  # inner list is a real vector reached through the tuple
    print(d)

main()
