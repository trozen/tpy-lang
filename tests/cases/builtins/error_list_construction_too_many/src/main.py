# list() with two positional arguments is refused by the shared no-match
# path, as CPython raises TypeError (list expected at most 1 argument).


def main() -> None:
    xs = [1, 2]
    print(list(xs, xs))  # tpyc: error(/takes 1 positional argument/)


main()
