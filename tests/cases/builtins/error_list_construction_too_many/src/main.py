# list() with two positional arguments is refused by the shared no-match
# path, as CPython raises TypeError (list expected at most 1 argument).


def main() -> None:
    xs = [1, 2]
    print(list(xs, xs))  # tpyc: error(/No matching overload for 'list' with argument types \(list\[int32\], list\[int32\]\)/)


main()
