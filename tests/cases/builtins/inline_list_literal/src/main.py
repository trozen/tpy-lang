# Inline list literals passed directly to builtin functions with protocol params

def main() -> None:
    # all/any
    print(all([True, True, True]))
    print(all([True, False, True]))
    print(any([False, False, False]))
    print(any([False, True, False]))

    # sum
    print(sum([1, 2, 3, 4]))

    # sorted
    print(sorted([3, 1, 4, 1, 5]))

    # str.join with inline literal
    print(",".join(["a", "b", "c"]))

main()
