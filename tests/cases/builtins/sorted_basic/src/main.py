# sorted() builtin on integer lists

def main() -> None:
    a = [3, 1, 4, 1, 5, 9, 2, 6]
    print(sorted(a))

    b = [5, 4, 3, 2, 1]
    print(sorted(b))

    c = [1, 2, 3]
    print(sorted(c))

    # empty
    empty: list[int] = []
    print(sorted(empty))

    # duplicates
    d = [1, 1, 1]
    print(sorted(d))

main()
