# len() takes its argument positionally only, as in CPython
# (TypeError: len() takes no keyword arguments).


def main() -> None:
    xs = [3, 1, 2]
    print(len(obj=xs))  # tpyc: error(/parameter 'obj' is positional-only and cannot be passed by keyword/)


main()
